"""
Testes de app/media.py + GET /media/{video_id} + `media_pronta` no catálogo.

É o que impede o bug que o player tinha: a API devolvia `hls_url` para
qualquer vídeo "completed", mesmo sem master.m3u8 no disco — o HLS.js entrava
num loop de retry num 404 ("só toca vídeo que tem URL dentro do JS").
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import media
from app.main import app

client = TestClient(app)


@pytest.fixture
def pasta_videos(tmp_path, monkeypatch):
    """VIDEOS_DIR isolado por teste (o do repositório não é tocado)."""
    monkeypatch.setattr(media, "VIDEOS_DIR", tmp_path)
    return tmp_path


def publicar_hls(pasta: "object", video_id: str, segmentos: int = 2) -> None:
    """Cria a estrutura que a transcodificação deixaria em videos/{id}/."""
    diretorio = pasta / video_id / "360p"
    diretorio.mkdir(parents=True, exist_ok=True)
    (pasta / video_id / "master.m3u8").write_text(
        "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=464000\n360p/playlist.m3u8\n",
        encoding="utf-8",
    )
    (diretorio / "playlist.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
    for indice in range(segmentos):
        (diretorio / f"segment_{indice:03d}.ts").write_bytes(b"\x47" + b"\x00" * 187)


# ---------------------------------------------------------------------------
# app/media.py
# ---------------------------------------------------------------------------

def test_master_existe_false_quando_nao_ha_midia(pasta_videos):
    assert media.master_existe("v-novo") is False


def test_master_existe_true_depois_da_transcodificacao(pasta_videos):
    publicar_hls(pasta_videos, "v-pronto")

    assert media.master_existe("v-pronto") is True


def test_master_existe_vazio_na_quebra(pasta_videos):
    assert media.master_existe("") is False
    assert media.master_existe(None) is False


def test_status_midia_conta_segmentos_e_thumbnail(pasta_videos):
    publicar_hls(pasta_videos, "v-pronto", segmentos=3)
    (pasta_videos / "v-pronto" / "thumbnail.jpg").write_bytes(b"\xff\xd8")

    status = media.status_midia_completo("v-pronto")

    assert status["media_pronta"] is True
    assert status["master_playlist"] is True
    assert status["thumbnail"] is True
    assert status["segmentos"] == 3


def test_status_midia_sem_arquivos(pasta_videos):
    status = media.status_midia("v-fantasma")

    assert status == {
        "video_id": "v-fantasma",
        "media_pronta": False,
        "master_playlist": False,
        "thumbnail": False,
        "diretorio": str(pasta_videos / "v-fantasma"),
    }


def test_garantir_diretorio_cria_a_pasta(tmp_path, monkeypatch):
    alvo = tmp_path / "ainda" / "nao" / "existe"
    monkeypatch.setattr(media, "VIDEOS_DIR", alvo)

    media.garantir_diretorio()

    assert alvo.is_dir()


# ---------------------------------------------------------------------------
# GET /media/{video_id}
# ---------------------------------------------------------------------------

def test_rota_media_devolve_o_retrato_do_disco(pasta_videos):
    publicar_hls(pasta_videos, "v-pronto", segmentos=1)

    corpo = client.get("/media/v-pronto").json()

    assert corpo["media_pronta"] is True
    assert corpo["segmentos"] == 1


def test_rota_media_para_video_sem_midia(pasta_videos):
    resposta = client.get("/media/v-fantasma")

    assert resposta.status_code == 200
    assert resposta.json()["media_pronta"] is False


# ---------------------------------------------------------------------------
# media_pronta no catálogo (o que o player lê)
# ---------------------------------------------------------------------------

@pytest.fixture
def banco(monkeypatch, pasta_videos):
    """Catálogo com dois vídeos concluídos: um com mídia, outro sem."""
    videos = {
        "v-com-midia": {
            "video_id": "v-com-midia", "titulo": "Com mídia", "tags": ["a"],
            "status": "completed",
        },
        "v-sem-midia": {
            "video_id": "v-sem-midia", "titulo": "Sem mídia", "tags": ["a"],
            "status": "completed",
        },
    }
    publicar_hls(pasta_videos, "v-com-midia")

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
        "app.recommendations.router.views_store.contar_visualizacoes", lambda video_id: 0
    )
    return videos


def test_catalogo_marca_media_pronta_por_video(banco):
    itens = {item["video_id"]: item for item in client.get("/catalogo").json()["videos"]}

    assert itens["v-com-midia"]["media_pronta"] is True
    assert itens["v-sem-midia"]["media_pronta"] is False
    # O hls_url continua previsível (contrato antigo) — a flag é que diz se toca.
    assert itens["v-sem-midia"]["hls_url"].endswith("/videos/v-sem-midia/master.m3u8")


def test_catalogo_prontos_filtra_sem_midia(banco):
    ids = [item["video_id"] for item in client.get("/catalogo?prontos=1").json()["videos"]]

    assert ids == ["v-com-midia"]


def test_catalogo_padrao_nao_filtra(banco):
    ids = {item["video_id"] for item in client.get("/catalogo").json()["videos"]}

    assert ids == {"v-com-midia", "v-sem-midia"}


def test_detalhe_do_video_traz_media_pronta(banco):
    assert client.get("/catalogo/v-com-midia").json()["media_pronta"] is True
    assert client.get("/catalogo/v-sem-midia").json()["media_pronta"] is False
