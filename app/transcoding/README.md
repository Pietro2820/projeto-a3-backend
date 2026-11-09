# Módulo de Transcodificação — `feature/transcodificacao` (João)

Transforma o vídeo cru recebido no upload em **streaming adaptativo de verdade**:
múltiplas resoluções (360p/480p/720p/1080p) empacotadas em **HLS** (`.m3u8` + `.ts`),
**master playlist** para o player trocar de qualidade sozinho, **thumbnail** do meio
do vídeo e armazenamento em **S3/MinIO**.

```
Vídeo original ──► FFmpeg ──► videos/{id}/360p/playlist.m3u8 + segment_*.ts
                       │      videos/{id}/480p/playlist.m3u8 + segment_*.ts
                       │      videos/{id}/720p/playlist.m3u8 + segment_*.ts
                       │      videos/{id}/1080p/playlist.m3u8 + segment_*.ts
                       │      videos/{id}/master.m3u8        (aponta as 4 variantes)
                       └───►  videos/{id}/thumbnail.jpg      (frame do meio)
```

## Estrutura do módulo

```
app/transcoding/
├── config.py            # escada de resoluções/bitrates, caminhos, nomes de arquivo
├── errors.py            # exceções próprias (TranscodingError, StorageError, ...)
├── ffmpeg_service.py    # coração do módulo: conversão, HLS, master, thumbnail
├── storage.py           # integração AWS S3 / MinIO (boto3)
├── task_adapter.py      # função que a task Celery do Pietro chama (contrato #3)
├── ESCOPO.md            # responsável, repositório, branch e regra de escopo
├── CONTRATO_TASK.md     # contrato de entrada/saída da task (levar pro Pietro)
└── tests/
    ├── conftest.py             # fixtures — inclui GERADOR de vídeo sintético
    ├── test_config.py          # números da escada de qualidade
    ├── test_ffmpeg_service.py  # conversão, HLS, master, thumbnail, erros
    ├── test_storage.py         # S3/MinIO simulado com moto (sem custo/sem conta)
    └── test_task_adapter.py    # contrato com a fila do Celery
```

## Pré-requisitos

### 1. FFmpeg instalado no sistema (programa, não lib Python)

| Sistema | Comando |
|---|---|
| Windows | `winget install Gyan.FFmpeg` (ou `choco install ffmpeg`) |
| macOS | `brew install ffmpeg` |
| Linux (Debian/Ubuntu) | `sudo apt update && sudo apt install ffmpeg` |

Verifique com `ffmpeg -version` e `ffprobe -version` (os dois precisam estar no PATH).

### 2. Dependências Python

```bash
pip install -r requirements.txt        # raiz do repo: já inclui pytest, boto3 e moto[s3]
```

> O guia individual sugere `pip install ffmpeg-python`. Este módulo usa
> **subprocess** direto (a doc técnica permite os dois: *"FFmpeg (via
> ffmpeg-python / subprocess)"*), então **não precisa** instalar ffmpeg-python.

## Não tem o vídeo do Rafael ainda? Sem problema

O FFmpeg **gera um vídeo de teste sintético** (padrão de cores + áudio). É assim
que os testes rodam sem depender de ninguém:

```bash
python scripts/gerar_video_teste.py                    # uploads/dev/original.mp4 (30s, 720p)
python scripts/gerar_video_teste.py -d 10 -o /tmp/t.mp4   # 10 segundos
```

Os testes usam a mesma técnica automaticamente (fixture `video_teste` no conftest).

## Rodando os testes

```bash
# na raiz do repositório
python -m pytest app/transcoding/tests -v
```

- Sem FFmpeg instalado, os testes de mídia são **pulados** (skip) com aviso — o resto roda.
- Sem `boto3`/`moto`, os testes de storage são pulados — o resto roda.
- A suíte inteira leva ~30s (o vídeo de teste tem 6s de propósito: ciclo rápido).

## Usando o serviço diretamente

```python
from app.transcoding import ffmpeg_service
from app.transcoding.config import RES_720P

# Passo 2a — começar SIMPLES: uma resolução só
ffmpeg_service.converter_para_resolucao("uploads/dev/original.mp4", "saida/v720.mp4", RES_720P)

# Thumbnail (Passo 5)
ffmpeg_service.extrair_thumbnail("uploads/dev/original.mp4", "saida/thumbnail.jpg")

# Pipeline completo: 4 resoluções + master.m3u8 + thumbnail (contrato #1)
resultado = ffmpeg_service.transcodificar("meu-video-id", "uploads/dev/original.mp4")
print(resultado["master_playlist"])  # videos/meu-video-id/master.m3u8
```

## Configuração S3/MinIO (Passo 6)

Tudo via variáveis de ambiente (`.env` — **nunca commitar**, usar `.env.example`):

```bash
ENABLE_S3=true                      # liga o upload pós-transcodificação
S3_BUCKET=videos-plataforma
AWS_ACCESS_KEY_ID=minioadmin        # credenciais
AWS_SECRET_ACCESS_KEY=minioadmin
AWS_ENDPOINT_URL=http://localhost:9000   # MinIO; para AWS real, NÃO defina
AWS_REGION=us-east-1
```

Subindo um MinIO local para testar de verdade:

```bash
docker run -d --name minio -p 9000:9000 -p 9001:9001 \
  -e MINIO_ROOT_USER=minioadmin -e MINIO_ROOT_PASSWORD=minioadmin \
  -v minio_data:/data quay.io/minio/minio server /data --console-address ":9001"

# criar o bucket (console em http://localhost:9001, login minioadmin/minioadmin)
# ou: docker exec minio mc alias set local http://localhost:9000 minioadmin minioadmin
#     docker exec minio mc mb local/videos-plataforma
```

Chaves usadas no bucket (contrato #1 da doc técnica):

```
uploads/{video_id}/original.{ext}
videos/{video_id}/{resolucao}/playlist.m3u8
videos/{video_id}/{resolucao}/segment_NNN.ts
videos/{video_id}/master.m3u8
videos/{video_id}/thumbnail.jpg
```

> **Content-Type importa**: `.m3u8` é enviado como `application/vnd.apple.mpegurl`
> e `.ts` como `video/mp2t`. Errado aqui, o player do Pedro não toca.

## Integração com o time

| Com quem | O quê | Onde está definido |
|---|---|---|
| **Rafael** (upload) | Lê o original de `uploads/{video_id}/original.{ext}` | contrato #1 da doc técnica |
| **Pietro** (fila) | Task chama `executar_transcodificacao(video_id, caminho)` | `CONTRATO_TASK.md` |
| **Pedro** (player) | Consome `videos/{video_id}/master.m3u8` (caminhos relativos) | formato HLS padrão + `RESOLUTION`/`BANDWIDTH` no master |
| **Pietro/Rafael** (Supabase) | Este módulo NÃO mexe em metadados — só arquivos | passo 6 do guia individual |

## Escada de qualidade (Passo 4 do guia)

| Resolução | Vídeo | Áudio | BANDWIDTH no master |
|---|---|---|---|
| 360p | 400 kbps | 64 kbps | 464.000 |
| 480p | 800 kbps | 96 kbps | 896.000 |
| 720p | 2500 kbps | 128 kbps | 2.628.000 |
| 1080p | 5000 kbps | 192 kbps | 5.192.000 |

- Aspect ratio original preservado (`scale=-2:altura`, largura sempre par).
- Codec: H.264 (`libx264`) + AAC, `yuv420p` — compatível com todos os navegadores.
- Segmentos de 4s com keyframes forçados (`-force_key_frames`) — cortes limpos.

## Integração com o que já existe no `develop`

| Módulo do time | Como encaixa |
|---|---|
| **Upload (Rafael)** — `app/upload/router.py` | Salva o original em `uploads/{video_id}/original.{ext}` (extensões aceitas: `.mp4 .avi .mov .webm`). Este módulo lê exatamente desse caminho (`UPLOAD_DIR`, mesma variável de ambiente dele) e a suíte testa os 4 containers. |
| **Fila (Pietro)** — `app/queue/tasks.py` | A task `transcodificar_video` já existe com retry/backoff. Trocar o placeholder por `from app.transcoding import processar` + `resultado = processar(video_id, caminho_original)` — snippet pronto em `CONTRATO_TASK.md`. |
| **Banco (Pietro)** — `app/database/videos.py` | Status (`pending/processing/completed/failed`) é atualizado pela task com `atualizar_status`. Este módulo não toca no Supabase. |
| **Player (Pedro)** | Consome `videos/{video_id}/master.m3u8` (caminhos relativos, `BANDWIDTH`/`RESOLUTION` presentes) — servível de qualquer raiz: local, S3, MinIO ou CDN. |
| **CI (GitHub Actions)** | O runner `ubuntu-latest` já tem FFmpeg pré-instalado; `boto3`/`moto` entraram no `requirements.txt`, então a suíte deste módulo roda inteira no pipeline sem mudar o `ci.yml`. |

## Checklist de entrega (do guia individual)

- [x] Conversão funcionando pra pelo menos 1 resolução (`converter_para_resolucao`)
- [x] HLS gerado (.m3u8 + .ts) (`gerar_hls` + `gerar_master_playlist`)
- [x] Thumbnail extraída (`extrair_thumbnail`, frame do meio)
- [x] Testes unitários da transcodificação (suíte em `tests/`, TDD)
- [x] Integração com S3/MinIO (`storage.py`, testada com moto)
- [ ] Contrato da task combinado com o Pietro (rascunho pronto em `CONTRATO_TASK.md` — falta a conversa)
- [ ] Rodar contra o vídeo REAL do Rafael + subir no MinIO/S3 do time
