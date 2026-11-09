# Contrato da Task de Transcodificação (Pietro ⇄ João)

Contrato #3 da documentação técnica, atualizado contra o código que **já existe**
no `develop` (`app/queue/tasks.py`, task `transcodificar_video`).
Qualquer mudança combinada deve ser atualizada aqui.

## A função que a fila chama

```python
from app.transcoding import processar   # apelido de executar_transcodificacao

resultado = processar(video_id, caminho_original)
```

- Localização: `app/transcoding/task_adapter.py`
- Assinatura: **idêntica** à que o placeholder do Pietro já previa
  (`transcoding_service.processar(video_id, caminho_original)`).
- **Nunca levanta exceção** — todo erro volta como `status: "failed"` no dict.
- **Idempotente**: rodar de novo com o mesmo `video_id` sobrescreve as saídas
  (FFmpeg com `-y`) → retry do Celery é seguro.

## Entrada

| Campo | Tipo | Descrição |
|---|---|---|
| `video_id` | `str` (UUID) | ID gerado no upload (módulo do Rafael) |
| `caminho_original` | `str` | `uploads/{video_id}/original.{ext}` (ext: mp4/avi/mov/webm) |

## Saída — sucesso

```json
{
  "status": "completed",
  "video_id": "...",
  "diretorio": "videos/{video_id}",
  "master_playlist": "videos/{video_id}/master.m3u8",
  "caminho_hls": "videos/{video_id}/master.m3u8",
  "playlists": {
    "360p":  {"playlist": ".../360p/playlist.m3u8",  "largura": 640,  "altura": 360,  "bandwidth": 464000},
    "480p":  {"playlist": ".../480p/playlist.m3u8",  "largura": 854,  "altura": 480,  "bandwidth": 896000},
    "720p":  {"playlist": ".../720p/playlist.m3u8",  "largura": 1280, "altura": 720,  "bandwidth": 2628000},
    "1080p": {"playlist": ".../1080p/playlist.m3u8", "largura": 1920, "altura": 1080, "bandwidth": 5192000}
  },
  "thumbnail": "videos/{video_id}/thumbnail.jpg",
  "s3": null
}
```

> `caminho_hls` é um apelido de `master_playlist`, mantido porque era a chave
> que o placeholder da task do Pietro já retornava — assim nada que dependa
> dela quebra.

Com `ENABLE_S3=true`, `s3` vem preenchido:

```json
{
  "s3": {
    "bucket": "...",
    "original": "uploads/{video_id}/original.mp4",
    "processados": ["videos/{video_id}/360p/playlist.m3u8", "..."],
    "master_playlist_url": "http://localhost:9000/{bucket}/videos/{video_id}/master.m3u8"
  }
}
```

## Saída — falha

```json
{
  "status": "failed",
  "video_id": "...",
  "error": "mensagem legível (FFmpeg, arquivo inválido, S3...)"
}
```

## Como plugar na task que JÁ EXISTE no develop

O `app/queue/tasks.py` do Pietro tem hoje um placeholder comentado. A troca é
de **3 linhas** (o resto — retry com backoff 60/300/900 — já está pronto lá):

```python
from app.transcoding import processar                       # ← novo import
from app.database.videos import atualizar_status           # ← já existe no repo

@celery_app.task(name="app.queue.tasks.transcodificar_video", bind=True,
                 max_retries=3, default_retry_delay=60)
def transcodificar_video(self, video_id: str, caminho_original: str):
    resultado = processar(video_id, caminho_original)       # ← linha real

    if resultado["status"] == "failed":
        atualizar_status(video_id, "failed")
        backoff = [60, 300, 900]
        raise self.retry(
            exc=RuntimeError(resultado["error"]),
            countdown=backoff[min(self.request.retries, len(backoff) - 1)],
        )

    atualizar_status(video_id, "completed")
    return resultado
```

### ⚠️ Combinado importante sobre retry

O adaptador do João **não levanta exceção** (devolve `status: "failed"`).
Por isso, para o backoff que já existe na task funcionar, ela precisa dar
`raise self.retry(...)` quando receber `failed` — exatamente como no snippet
acima. Se a task só retornar o dict, o retry nunca dispara.

## Status no banco (Supabase)

Quem atualiza é a **task do Pietro**, com `app.database.videos.atualizar_status`
(já existe no repo): `pending → processing → completed / failed`.
O módulo de transcodificação **não conhece o banco** (separação de responsabilidades).

## Combinados em aberto (definir na conversa)

1. **Timeout** da task no Celery (`time_limit` / `soft_time_limit`) — vídeo longo
   pode passar de minutos; sugerido valor generoso (ex: 30 min).
2. **S3 ligado/desligado** por ambiente (`ENABLE_S3` no `.env`) e quem sobe o
   MinIO compartilhado do time.
3. Manter o **original no storage** após processar, ou remover? (Hoje, com S3
   ligado, o adapter também sobe o original.)
4. Quem chama `processar_upload` → `transcodificar_video.delay(...)`: o endpoint
   do Rafael ainda não dispara a fila (o upload retorna `pending` e para aí).
   Fica pro PR seguinte dele ou pro Pietro plugar no `/upload`.
