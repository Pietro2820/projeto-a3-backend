"""
Fixtures compartilhadas dos testes de transcodificação.

O pulo do gato (é o que permite fazer TDD ANTES do vídeo do Rafael existir):
a fixture `video_teste` gera um vídeo sintético com o próprio FFmpeg —
padrão de cores testsrc2 (com contador de frames) + áudio senoidal. Nenhum
teste depende de arquivo de vídeo real; quando o vídeo de verdade chegar,
os mesmos testes valem para ele.

Organização pensada para servir de referência pro time (pedido do guia):
    video_teste              -> sessão inteira (gerar vídeo é caro; 1x basta)
    hls_360p                 -> módulo (vários testes inspecionam o mesmo HLS)
    transcodificacao_completa-> sessão (pipeline completo rodado 1x)
    diretorio_saida          -> por teste (cada teste escreve no seu tmp)
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.transcoding import ffmpeg_service
from app.transcoding.config import RES_360P

# Vídeo curto de propósito: o guia avisa que transcodificar vídeo grande
# trava o ciclo de desenvolvimento. 6s cobrem todos os casos de teste.
TEMPO_VIDEO_TESTE = 6
LARGURA_VIDEO_TESTE = 1280
ALTURA_VIDEO_TESTE = 720


def gerar_video_sintetico(destino: Path, duracao: int = TEMPO_VIDEO_TESTE) -> Path:
    """Gera um vídeo .mp4 de teste (vídeo testsrc2 + áudio sine) com FFmpeg."""
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi",
            "-i", f"testsrc2=size={LARGURA_VIDEO_TESTE}x{ALTURA_VIDEO_TESTE}:rate=30:duration={duracao}",
            "-f", "lavfi",
            "-i", f"sine=frequency=440:sample_rate=44100:duration={duracao}",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest",
            str(destino),
        ],
        check=True,
        capture_output=True,
    )
    return destino


@pytest.fixture(scope="session")
def video_teste(tmp_path_factory) -> Path:
    """Vídeo sintético 1280x720, ~6s, com áudio — base de todos os testes."""
    if not ffmpeg_service.ffmpeg_disponivel():
        pytest.skip("FFmpeg não instalado no sistema (veja app/transcoding/README.md)")
    return gerar_video_sintetico(tmp_path_factory.mktemp("midia") / "original.mp4")


@pytest.fixture(scope="session")
def transcodificacao_completa(video_teste, tmp_path_factory) -> dict:
    """Pipeline completo (escada inteira + master + thumbnail) rodado 1x."""
    destino = tmp_path_factory.mktemp("pipeline") / "videos" / "video-fixture"
    return ffmpeg_service.transcodificar("video-fixture", video_teste, diretorio_saida=destino)


@pytest.fixture(scope="module")
def hls_360p(video_teste, tmp_path_factory) -> Path:
    """Pacote HLS 360p gerado 1x por módulo (vários testes o inspecionam)."""
    diretorio = tmp_path_factory.mktemp("hls") / "360p"
    return ffmpeg_service.gerar_hls(video_teste, diretorio, RES_360P)


@pytest.fixture
def diretorio_saida(tmp_path) -> Path:
    """Pasta de saída isolada por teste."""
    return tmp_path / "saida"
