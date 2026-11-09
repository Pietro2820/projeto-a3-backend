"""
Testes da mídia no BUCKET público (Supabase Storage / S3).

Cenário que estes testes blindam: o worker transcodificou com ENABLE_S3=true e
o HLS mora no bucket — a máquina da API NÃO tem os arquivos no disco. Antes, o
catálogo montava o `hls_url` sempre apontando para o disco local (404) e
`media_pronta` dava False: "os vídeos do Supabase não tocam no player".

Rede é dublada em `media._head_ok` (nenhum teste sai da máquina) e o cache de
HEADs é limpo entre testes.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import media
from app.main import app

client = TestClient(app)

BASE_BUCKET = "https://projeto.supabase.co/storage/v1/object/public/streaming"


def publicar_hls_local(pasta, video_id: str) -> None:
    """Estrutura mínima que a transcodificação deixaria em videos/{id}/."""
    diretorio = pasta / video_id / "360p"
    diretorio.mkdir(parents=True, exist_ok=True)
    (pasta / video_id / "master.m3u8").write_text(
        "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=464000\n360p/playlist.m3u8\n",
        encoding="utf-8",
    )
    (diretorio / "playlist.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
    (diretorio / "segment_000.ts").write_bytes(b"\x47" + b"\x00" * 187)


@pytest.fixture
def ambiente_s3(tmp_path, monkeypatch):
    """VIDEOS_DIR isolado + S3 público configurado + HEAD dublado.

    `head_calls` registra as URLs consultadas (prova o cache e a precedência
    do disco); `remotas` controla quais chaves "existem" no bucket.
    """
    monkeypatch.setattr(media, "VIDEOS_DIR", tmp_path)
    monkeypatch.setenv("S3_PUBLIC_URL", BASE_BUCKET)
    monkeypatch.delenv("ENABLE_S3", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("S3_BUCKET", raising=False)
    media.limpar_cache_remota()

    estado = {"head_calls": [], "remotas": {"v-remoto"}}

    def head_falso(url, timeout=3.0):
        estado["head_calls"].append(url)
        video_id = url.rsplit("/videos/", 1)[-1].split("/", 1)[0]
        return video_id in estado["remotas"]

    monkeypatch.setattr(media, "_head_ok", head_falso)
    yield estado
    media.limpar_cache_remota()


# ---------------------------------------------------------------------------
# base pública: S3_PUBLIC_URL explícita, derivação Supabase e desligado
# ---------------------------------------------------------------------------

def test_base_publica_usa_s3_public_url(ambiente_s3):
    assert media.base_publica_s3() == BASE_BUCKET


def test_base_publica_derivada_do_supabase(monkeypatch, tmp_path):
    """S3_PUBLIC_URL vazia (como no .env.example) + ENABLE_S3 + SUPABASE_URL
    + S3_BUCKET: a API deriva sozinha a URL pública documentada do Supabase."""
    monkeypatch.setattr(media, "VIDEOS_DIR", tmp_path)
    monkeypatch.delenv("S3_PUBLIC_URL", raising=False)
    monkeypatch.setenv("ENABLE_S3", "true")
    monkeypatch.setenv("SUPABASE_URL", "https://abc123.supabase.co")
    monkeypatch.setenv("S3_BUCKET", "streaming")

    assert media.base_publica_s3() == (
        "https://abc123.supabase.co/storage/v1/object/public/streaming"
    )


def test_base_publica_none_sem_configuracao(monkeypatch, tmp_path):
    """Dev local sem S3: nada muda — a mídia continua vindo só do disco."""
    monkeypatch.setattr(media, "VIDEOS_DIR", tmp_path)
    for var in ("S3_PUBLIC_URL", "ENABLE_S3", "SUPABASE_URL", "S3_BUCKET"):
        monkeypatch.delenv(var, raising=False)

    assert media.base_publica_s3() is None
    assert media.master_existe_remoto("v-remoto") is False


# ---------------------------------------------------------------------------
# origem da mídia: disco vence bucket; bucket cobre disco vazio
# ---------------------------------------------------------------------------

def test_origem_s3_quando_so_existe_no_bucket(ambiente_s3):
    assert media.origem_midia("v-remoto") == "s3"
    assert media.master_existe("v-remoto") is True


def test_origem_local_vence_e_nao_consulta_rede(ambiente_s3):
    """Disco primeiro: o mesmo id existe nos dois lugares e nada vai à rede."""
    publicar_hls_local(media.VIDEOS_DIR, "v-remoto")

    assert media.origem_midia("v-remoto") == "local"
    assert ambiente_s3["head_calls"] == []


def test_origem_none_quando_nao_existe_em_lugar_nenhum(ambiente_s3):
    assert media.origem_midia("v-fantasma") is None
    assert media.master_existe("v-fantasma") is False


def test_cache_evita_head_repetido(ambiente_s3):
    assert media.master_existe_remoto("v-remoto") is True
    assert media.master_existe_remoto("v-remoto") is True
    assert media.master_existe_remoto("v-remoto") is True

    assert len(ambiente_s3["head_calls"]) == 1


def test_head_ok_negativo_tambem_vai_para_o_cache(ambiente_s3):
    assert media.master_existe_remoto("v-fantasma") is False
    assert media.master_existe_remoto("v-fantasma") is False

    assert len(ambiente_s3["head_calls"]) == 1


# ---------------------------------------------------------------------------
# GET /media/{video_id} — o diagnóstico que o player usa para explicar falhas
# ---------------------------------------------------------------------------

def test_rota_media_mostra_origem_s3(ambiente_s3):
    corpo = client.get("/media/v-remoto").json()

    assert corpo["media_pronta"] is True
    assert corpo["origem"] == "s3"
    assert corpo["s3_master_url"] == f"{BASE_BUCKET}/videos/v-remoto/master.m3u8"
    assert corpo["s3_master_existe"] is True


def test_rota_media_sem_midia_em_lugar_nenhum(ambiente_s3):
    corpo = client.get("/media/v-fantasma").json()

    assert corpo["media_pronta"] is False
    assert corpo["origem"] is None


def test_rota_media_sem_s3_nao_vaza_campos_remotos(monkeypatch, tmp_path):
    monkeypatch.setattr(media, "VIDEOS_DIR", tmp_path)
    for var in ("S3_PUBLIC_URL", "ENABLE_S3", "SUPABASE_URL", "S3_BUCKET"):
        monkeypatch.delenv(var, raising=False)

    corpo = client.get("/media/v-local").json()

    assert "s3_master_url" not in corpo
    # O contrato antigo de status_midia continua com as mesmas chaves.
    assert set(corpo) == {
        "video_id", "media_pronta", "master_playlist", "thumbnail",
        "diretorio", "segmentos", "origem",
    }


# ---------------------------------------------------------------------------
# Catálogo: hls_url apontando para o bucket (o que faz o player tocar)
# ---------------------------------------------------------------------------

@pytest.fixture
def banco_misto(ambiente_s3, monkeypatch):
    """Dois vídeos completed: um só no bucket, um só no disco da API."""
    publicar_hls_local(media.VIDEOS_DIR, "v-local")
    videos = {
        "v-remoto": {
            "video_id": "v-remoto", "titulo": "Vídeo do bucket", "tags": ["a"],
            "status": "completed",
        },
        "v-local": {
            "video_id": "v-local", "titulo": "Vídeo do disco", "tags": ["a"],
            "status": "completed",
        },
        "v-fantasma": {
            "video_id": "v-fantasma", "titulo": "Sem mídia", "tags": ["a"],
            "status": "completed",
        },
    }
    monkeypatch.setattr(
        "app.recommendations.router.listar_videos",
        lambda status=None: [
            video for video in videos.values()
            if status is None or video["status"] == status
        ],
    )
    monkeypatch.setattr(
        "app.recommendations.router.buscar_video", lambda video_id: videos.get(video_id)
    )
    monkeypatch.setattr(
        "app.recommendations.router.views_store.contar_visualizacoes",
        lambda video_id: 0,
    )
    return videos


def test_catalogo_aponta_hls_url_para_o_bucket(banco_misto):
    itens = {i["video_id"]: i for i in client.get("/catalogo").json()["videos"]}

    remoto = itens["v-remoto"]
    assert remoto["media_pronta"] is True
    assert remoto["origem_midia"] == "s3"
    assert remoto["hls_url"] == f"{BASE_BUCKET}/videos/v-remoto/master.m3u8"
    assert remoto["thumbnail_url"] == f"{BASE_BUCKET}/videos/v-remoto/thumbnail.jpg"


def test_catalogo_mantem_url_local_para_midia_local(banco_misto):
    itens = {i["video_id"]: i for i in client.get("/catalogo").json()["videos"]}

    local = itens["v-local"]
    assert local["origem_midia"] == "local"
    assert local["hls_url"] == "http://testserver/videos/v-local/master.m3u8"


def test_catalogo_sem_midia_segue_marcado_como_nao_pronto(banco_misto):
    itens = {i["video_id"]: i for i in client.get("/catalogo").json()["videos"]}

    assert itens["v-fantasma"]["media_pronta"] is False
    assert itens["v-fantasma"]["origem_midia"] is None
    # URL previsível (contrato antigo), mas a flag diz que não toca.
    assert itens["v-fantasma"]["hls_url"].endswith("/videos/v-fantasma/master.m3u8")


def test_prontos_inclui_video_do_bucket(banco_misto):
    ids = {i["video_id"] for i in client.get("/catalogo?prontos=1").json()["videos"]}

    assert ids == {"v-remoto", "v-local"}


def test_detalhe_do_video_do_bucket(banco_misto):
    corpo = client.get("/catalogo/v-remoto").json()

    assert corpo["media_pronta"] is True
    assert corpo["hls_url"] == f"{BASE_BUCKET}/videos/v-remoto/master.m3u8"


def test_relacionados_usam_a_mesma_origem(banco_misto):
    corpo = client.get("/videos/v-local/relacionados").json()
    relacionados = {i["video_id"]: i for i in corpo.get("relacionados", [])}

    assert relacionados["v-remoto"]["hls_url"] == (
        f"{BASE_BUCKET}/videos/v-remoto/master.m3u8"
    )
