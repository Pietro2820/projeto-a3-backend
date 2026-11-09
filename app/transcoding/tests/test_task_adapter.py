"""
Testes do adaptador da task Celery (Passo 7 — contrato com o Pietro).

Aqui o serviço de FFmpeg é mockado na maior parte dos testes: o que está em
jogo é o CONTRATO (entrada/saída, tratamento de erro, integração S3), não a
conversão em si — essa já é coberta por test_ffmpeg_service.py.

Há também um teste de fumaça real (marcado pelo skip quando não há FFmpeg)
que executa o adaptador de ponta a ponta com o vídeo sintético em 360p.
"""
from pathlib import Path

import pytest

from app.transcoding import ffmpeg_service, task_adapter
from app.transcoding.config import RES_360P
from app.transcoding.errors import TranscodingError

sem_ffmpeg = pytest.mark.skipif(
    not ffmpeg_service.ffmpeg_disponivel(),
    reason="FFmpeg não instalado (instruções em app/transcoding/README.md)",
)

VIDEO_ID = "550e8400-e29b-41d4-a716-446655440000"


def _resultado_fake(diretorio: Path) -> dict:
    """Dict de retorno no formato real do ffmpeg_service.transcodificar."""
    return {
        "status": "completed",
        "video_id": VIDEO_ID,
        "diretorio": str(diretorio),
        "master_playlist": str(diretorio / "master.m3u8"),
        "playlists": {
            "360p": {
                "playlist": str(diretorio / "360p" / "playlist.m3u8"),
                "largura": 640,
                "altura": 360,
                "bandwidth": RES_360P.bandwidth,
            }
        },
        "thumbnail": str(diretorio / "thumbnail.jpg"),
    }


@pytest.fixture
def pasta_processada(tmp_path) -> Path:
    """Cria em disco uma 'saída de transcodificação' fake para os testes de S3."""
    diretorio = tmp_path / "videos" / VIDEO_ID
    (diretorio / "360p").mkdir(parents=True)
    (diretorio / "360p" / "playlist.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
    (diretorio / "master.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
    (diretorio / "thumbnail.jpg").write_bytes(b"\xff\xd8\xff\xe0")
    return diretorio


# ---------------------------------------------------------------------------
# Contrato de saída (o que o Pietro recebe de volta)
# ---------------------------------------------------------------------------
def test_saida_completed_segundo_contrato(monkeypatch, pasta_processada):
    monkeypatch.setattr(
        ffmpeg_service, "transcodificar", lambda **kwargs: _resultado_fake(pasta_processada)
    )
    resultado = task_adapter.executar_transcodificacao(VIDEO_ID, "/uploads/x/original.mp4")

    assert resultado["status"] == "completed"
    assert resultado["video_id"] == VIDEO_ID
    assert resultado["master_playlist"].endswith("master.m3u8")
    assert "360p" in resultado["playlists"]
    assert resultado["s3"] is None  # S3 desligado por padrão em dev


def test_entrada_e_repasada_ao_servico(monkeypatch):
    recebidos = {}

    def fake(video_id, caminho_original, resolucoes=None):
        recebidos.update({"video_id": video_id, "caminho": caminho_original})
        return _resultado_fake(Path("/tmp"))

    monkeypatch.setattr(ffmpeg_service, "transcodificar", fake)
    task_adapter.executar_transcodificacao(VIDEO_ID, "uploads/abc/original.mp4")

    assert recebidos == {"video_id": VIDEO_ID, "caminho": "uploads/abc/original.mp4"}


def test_saida_failed_quando_servico_levanta_erro_de_transcodificacao(monkeypatch):
    def fake(**kwargs):
        raise TranscodingError("FFmpeg falhou: codec não suportado")

    monkeypatch.setattr(ffmpeg_service, "transcodificar", fake)
    resultado = task_adapter.executar_transcodificacao(VIDEO_ID, "/uploads/x/original.mp4")

    assert resultado["status"] == "failed"
    assert resultado["video_id"] == VIDEO_ID
    assert "codec não suportado" in resultado["error"]


def test_saida_failed_mesmo_para_erro_inesperado(monkeypatch):
    """A task nunca explode pro Celery sem devolver o formato do contrato."""
    def fake(**kwargs):
        raise RuntimeError("bug inesperado")

    monkeypatch.setattr(ffmpeg_service, "transcodificar", fake)
    resultado = task_adapter.executar_transcodificacao(VIDEO_ID, "/uploads/x/original.mp4")

    assert resultado["status"] == "failed"
    assert "inesperado" in resultado["error"]


# ---------------------------------------------------------------------------
# Integração S3 ligada (ENABLE_S3=true)
# ---------------------------------------------------------------------------
def test_upload_s3_quando_habilitado(monkeypatch, pasta_processada, tmp_path):
    boto3 = pytest.importorskip("boto3")
    pytest.importorskip("moto")
    from moto import mock_aws

    from app.transcoding.storage import VideoStorage

    original = tmp_path / "original.mp4"
    original.write_bytes(b"\x00" * 128)

    monkeypatch.setenv("ENABLE_S3", "true")
    monkeypatch.setenv("S3_BUCKET", "balde-teste")

    monkeypatch.setattr(
        ffmpeg_service, "transcodificar", lambda **kwargs: _resultado_fake(pasta_processada)
    )

    with mock_aws():
        cliente = boto3.client("s3", region_name="us-east-1")
        cliente.create_bucket(Bucket="balde-teste")

        # O adapter chama VideoStorage.from_env() -> devolvemos uma instância
        # já apontada para o S3 fake do moto (sem depender de credenciais reais).
        monkeypatch.setattr(
            VideoStorage, "from_env",
            classmethod(lambda cls: cls(bucket="balde-teste", client=cliente)),
        )

        resultado = task_adapter.executar_transcodificacao(VIDEO_ID, str(original))

    assert resultado["status"] == "completed"
    assert resultado["s3"]["bucket"] == "balde-teste"
    assert resultado["s3"]["original"] == f"uploads/{VIDEO_ID}/original.mp4"
    assert f"videos/{VIDEO_ID}/master.m3u8" in resultado["s3"]["processados"]
    assert "master.m3u8" in resultado["s3"]["master_playlist_url"]


def test_falha_no_upload_s3_vira_status_failed(monkeypatch, pasta_processada, tmp_path):
    original = tmp_path / "original.mp4"
    original.write_bytes(b"\x00" * 128)

    monkeypatch.setenv("ENABLE_S3", "true")
    monkeypatch.delenv("S3_BUCKET", raising=False)  # from_env vai falhar

    monkeypatch.setattr(
        ffmpeg_service, "transcodificar", lambda **kwargs: _resultado_fake(pasta_processada)
    )

    resultado = task_adapter.executar_transcodificacao(VIDEO_ID, str(original))

    assert resultado["status"] == "failed"
    assert "S3" in resultado["error"]


def test_s3_desligado_por_padrao(monkeypatch):
    monkeypatch.delenv("ENABLE_S3", raising=False)
    assert task_adapter.s3_habilitado() is False


@pytest.mark.parametrize("valor", ["1", "true", "TRUE", "yes", "sim"])
def test_s3_habilitado_aceita_valores_comuns(monkeypatch, valor):
    monkeypatch.setenv("ENABLE_S3", valor)
    assert task_adapter.s3_habilitado() is True


# ---------------------------------------------------------------------------
# Teste de fumaça real (sem mocks): adaptador de ponta a ponta em 360p
# ---------------------------------------------------------------------------
@sem_ffmpeg
def test_execucao_real_ponta_a_ponta(video_teste, tmp_path, monkeypatch):
    """Transcodifica de verdade (360p, rápido) e valida o contrato na saída."""
    monkeypatch.delenv("ENABLE_S3", raising=False)
    monkeypatch.setattr(ffmpeg_service, "BASE_VIDEOS", tmp_path / "videos")

    resultado = task_adapter.executar_transcodificacao(
        "video-real-360p", str(video_teste), resolucoes=[RES_360P]
    )

    assert resultado["status"] == "completed"
    assert Path(resultado["master_playlist"]).is_file()
    assert Path(resultado["thumbnail"]).is_file()
    playlist = Path(resultado["playlists"]["360p"]["playlist"])
    assert playlist.is_file()
    assert playlist.read_text(encoding="utf-8").startswith("#EXTM3U")
