"""
Camada de mídia (arquivos HLS gerados pela transcodificação).

Por que este módulo existe
--------------------------
O player (js/player.js) só consegue dar play num vídeo quando o
`videos/{video_id}/master.m3u8` existe de verdade no disco. Antes, a API
montava o `hls_url` a partir do `video_id` SEM verificar o arquivo: um vídeo
com status "completed" no banco mas sem HLS (transcodificação apagada, pasta
`videos/` fora do git, worker que falhou no meio) entrava no catálogo, o
player tentava carregar e ficava num loop infinito de retry num 404 — o
sintoma que o time descreveu como "só roda vídeo que tem URL dentro do JS".

Aqui ficam:
  * `VIDEOS_DIR`  — fonte única de verdade do diretório de mídia (a mesma
    variável `VIDEOS_DIR` usada por app/main.py e app/transcoding/config.py);
  * `status_midia(video_id)` — o que existe no disco para aquele vídeo;
  * `midia_pronta(video_id)` — atalho booleano usado pelo catálogo.

Mídia no BUCKET (Supabase Storage / S3)
---------------------------------------
Com o worker transcodificando em outra máquina (ou com `ENABLE_S3=true` e a
pasta `videos/` local limpa), o HLS de verdade mora no bucket — e a API não
tem NADA no disco. Este módulo também resolve esse caso:

  * `base_publica_s3()` — base pública de leitura do bucket (S3_PUBLIC_URL do
    .env; sem ela, deriva de SUPABASE_URL + S3_BUCKET quando ENABLE_S3=true);
  * `origem_midia(video_id)` — "local" | "s3" | None: onde está o master.m3u8
    que o HLS.js vai tocar (a verificação remota é um HEAD com cache TTL —
    o catálogo não fica refazendo rede a cada request);
  * `master_existe` / `status_midia` consideram as DUAS origens.

Assim o `/catalogo` devolve `hls_url` apontando para o bucket quando a mídia
está lá — antes apontava sempre para o disco da API, dava 404 e "os vídeos do
Supabase não tocavam".

Nenhuma função levanta exceção por arquivo ausente: mídia que não existe é
informação ("ainda não está pronta"), não erro.
"""
from __future__ import annotations

import os
import time
import urllib.request
from pathlib import Path

# Raiz do repositório (este arquivo fica em app/media.py).
RAIZ_PROJETO = Path(__file__).resolve().parent.parent

# Mesmo contrato de app/transcoding/config.py (BASE_VIDEOS) e do mount /videos
# de app/main.py: a pasta que guarda videos/{id}/master.m3u8, playlists,
# segmentos .ts e thumbnail.jpg.
VIDEOS_DIR = Path(os.getenv("VIDEOS_DIR", RAIZ_PROJETO / "videos"))

NOME_MASTER = "master.m3u8"
NOME_THUMBNAIL = "thumbnail.jpg"

# ---------------------------------------------------------------------------
# Bucket público (Supabase Storage / S3) — onde o worker publica o HLS quando
# ENABLE_S3=true. As chaves seguem o contrato #1 de app/transcoding/storage.py:
#   videos/{video_id}/master.m3u8 · videos/{video_id}/{res}/playlist.m3u8 · ...
# ---------------------------------------------------------------------------

# Mesmo critério de app/transcoding/task_adapter.py::s3_habilitado.
_VALORES_VERDADEIROS = {"1", "true", "yes", "sim"}

# O /catalogo chama a checagem remota para CADA vídeo sem mídia local: o cache
# evita uma enxurrada de HEADs a cada request (e um vídeo que acabou de subir
# para o bucket aparece em até TTL segundos — irrelevante para o fluxo real,
# em que o status só vira "completed" DEPOIS do upload terminar).
CACHE_REMOTA_TTL_SEGUNDOS = 30.0
_cache_remota: dict[str, tuple[float, bool]] = {}


def s3_habilitado() -> bool:
    """True com ENABLE_S3=true (worker publica o HLS no bucket)."""
    return (os.getenv("ENABLE_S3") or "").strip().lower() in _VALORES_VERDADEIROS


def base_publica_s3() -> str | None:
    """Base pública de leitura do bucket (sem barra final), ou None.

    Ordem:
      1. `S3_PUBLIC_URL` do .env — ex.: CDN, ou o formato público do Supabase:
         `https://<projeto>.supabase.co/storage/v1/object/public/<bucket>`;
      2. derivação automática com `ENABLE_S3=true` + `SUPABASE_URL` +
         `S3_BUCKET` (o formato acima é o documentado pelo Supabase) — assim
         funciona mesmo com o `S3_PUBLIC_URL` vazio do .env.example.

    Sem nenhuma das duas, a mídia é servida só do disco da API (dev local).
    """
    base = (os.getenv("S3_PUBLIC_URL") or "").strip().rstrip("/")
    if base:
        return base

    supabase_url = (os.getenv("SUPABASE_URL") or "").strip().rstrip("/")
    bucket = (os.getenv("S3_BUCKET") or "").strip()
    if s3_habilitado() and supabase_url and bucket:
        return f"{supabase_url}/storage/v1/object/public/{bucket}"
    return None


def url_publica_midia(video_id: str, arquivo: str = NOME_MASTER) -> str | None:
    """URL pública de um arquivo do vídeo no bucket (None sem S3 público)."""
    base = base_publica_s3()
    if not base or not video_id:
        return None
    return f"{base}/videos/{video_id}/{arquivo}"


def _head_ok(url: str, timeout: float = 3.0) -> bool:
    """HTTP HEAD → True só com resposta 2xx.

    Função isolada de propósito: os testes dublam este ponto e nenhuma outra
    parte do módulo precisa saber de rede. Falha de rede/4xx = "não existe".
    """
    try:
        requisicao = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(requisicao, timeout=timeout) as resposta:
            return 200 <= getattr(resposta, "status", 200) < 300
    except Exception:
        return False


def limpar_cache_remota() -> None:
    """Esvazia o cache de HEADs (testes e scripts de diagnóstico)."""
    _cache_remota.clear()


def master_existe_remoto(video_id: str) -> bool:
    """O master.m3u8 existe no bucket público? (HEAD com cache TTL)."""
    url = url_publica_midia(video_id)
    if not url:
        return False

    agora = time.monotonic()
    em_cache = _cache_remota.get(url)
    if em_cache is not None and (agora - em_cache[0]) < CACHE_REMOTA_TTL_SEGUNDOS:
        return em_cache[1]

    existe = _head_ok(url)
    _cache_remota[url] = (agora, existe)
    return existe


def origem_midia(video_id: str) -> str | None:
    """Onde está o master.m3u8 que o HLS.js vai tocar.

    "local" — no disco da API (mont /videos, sem CORS, mais rápido);
    "s3"    — só no bucket público (vídeo transcodificado pelo worker com
              ENABLE_S3=true; a API pode não ter cópia local);
    None    — em nenhum dos dois (ainda não está pronto para reprodução).
    """
    if not video_id:
        return None
    if caminho_master(video_id).is_file():
        return "local"
    if master_existe_remoto(video_id):
        return "s3"
    return None


def garantir_diretorio() -> Path:
    """Cria a pasta de mídia se ela não existir (StaticFiles exige que exista).

    Em dev ela só é criada na primeira transcodificação — sem isso o mount
    `/videos` derrubava a subida da API.
    """
    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    return VIDEOS_DIR


def diretorio_do_video(video_id: str) -> Path:
    """Caminho da pasta `videos/{video_id}/` (não garante que exista)."""
    return garantir_diretorio() / str(video_id)


def caminho_master(video_id: str) -> Path:
    return diretorio_do_video(video_id) / NOME_MASTER


def caminho_thumbnail(video_id: str) -> Path:
    return diretorio_do_video(video_id) / NOME_THUMBNAIL


def master_existe(video_id: str) -> bool:
    """True se o master.m3u8 do vídeo está no disco OU no bucket público.

    É o que o HLS.js toca: sem ele em nenhuma das duas origens, o vídeo não
    está pronto para reprodução (e o `hls_url` daria 404).
    """
    return origem_midia(video_id) is not None


def contar_segmentos(video_id: str) -> int:
    """Quantos segmentos .ts existem para o vídeo (0 = playlist sem mídia)."""
    pasta = diretorio_do_video(video_id)
    if not pasta.is_dir():
        return 0
    return sum(1 for arquivo in pasta.rglob("*.ts") if arquivo.is_file())


def status_midia(video_id: str) -> dict:
    """Retrato de onde está a mídia de um vídeo (disco e/ou bucket).

    Usado pelo campo `media_pronta` do catálogo e como base do
    GET /media/{video_id}. O player consulta isso para decidir entre dar play,
    avisar que ainda está transcodificando ou explicar que o arquivo HLS não
    foi encontrado — em vez de ficar tentando um 404 para sempre.

    Barato de propósito: `stat` no disco e, só quando não há master local E o
    bucket público está configurado, um HEAD com cache de `CACHE_REMOTA_TTL_
    SEGUNDOS`. O /catalogo chama isto para CADA vídeo da lista, e o cache é o
    que impede N requisições de rede por request. A contagem de segmentos fica
    só no detalhe (`status_midia_completo`).

    As chaves do dict são o contrato exato consumido pelos testes e pelo
    player — não acrescentar campos aqui (o detalhe com `origem`/S3 fica em
    `status_midia_completo`).
    """
    pronta = master_existe(video_id)
    master = caminho_master(video_id) if video_id else None
    thumbnail = caminho_thumbnail(video_id) if video_id else None

    return {
        "video_id": video_id,
        "media_pronta": pronta,
        "master_playlist": pronta,
        "thumbnail": bool(thumbnail and thumbnail.is_file()),
        "diretorio": str(diretorio_do_video(video_id)) if video_id else None,
    }


def status_midia_completo(video_id: str) -> dict:
    """`status_midia` + contagem de segmentos .ts (rota de detalhe/diagnóstico).

    Acrescenta `origem` ("local" | "s3" | None) e, com bucket público
    configurado, as URLs remotas — é o que o player e o
    `scripts/verificar_player.py` mostram ao explicar uma falha.
    """
    status = status_midia(video_id)
    status["segmentos"] = contar_segmentos(video_id)
    status["origem"] = origem_midia(video_id)

    if base_publica_s3() and video_id:
        status["s3_master_url"] = url_publica_midia(video_id)
        status["s3_thumbnail_url"] = url_publica_midia(video_id, NOME_THUMBNAIL)
        status["s3_master_existe"] = master_existe_remoto(video_id)

    return status


def midia_pronta(video_id: str) -> bool:
    """Atalho: o vídeo tem master.m3u8 no disco?"""
    return status_midia(video_id)["media_pronta"]
