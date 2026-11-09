"""
Configurações centrais do módulo de transcodificação.

Tudo que é "número mágico" do módulo mora aqui: escada de resoluções,
bitrates, caminhos base e tamanho de segmento HLS. Assim os testes e o
resto do time consultam uma fonte única de verdade.

Variáveis de ambiente suportadas:
    UPLOAD_DIR            onde ficam os arquivos originais (padrão: "uploads")
                          — MESMO nome de variável do módulo de upload (Rafael)
    VIDEOS_DIR            onde ficam os processados/HLS   (padrão: "videos")
    HLS_SEGMENT_SECONDS   duração de cada segmento .ts   (padrão: 4)
    FFMPEG_PRESET         preset do x264                 (padrão: "veryfast")
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Resolucao:
    """Um degrau da escada de qualidade (resolução + bitrates).

    Attributes:
        nome: nome curto usado nas pastas e no master.m3u8 (ex: "720p").
        altura: altura alvo em pixels; a largura é calculada pelo FFmpeg
            (scale=-2:altura) para manter o aspect ratio original.
        video_bitrate: bitrate alvo de vídeo (ex: "2500k").
        audio_bitrate: bitrate alvo de áudio (ex: "128k").
    """

    nome: str
    altura: int
    video_bitrate: str
    audio_bitrate: str

    @property
    def bandwidth(self) -> int:
        """Largura de banda total em bps (vídeo + áudio).

        É o valor que vai no atributo BANDWIDTH do master.m3u8 — o HLS.js
        (player do Pedro) usa isso para escolher a variante certa.
        """
        return (_para_kbps(self.video_bitrate) + _para_kbps(self.audio_bitrate)) * 1000


def _para_kbps(bitrate: str) -> int:
    """Converte strings de bitrate do FFmpeg para kbps. "2500k" -> 2500, "1M" -> 1000."""
    valor = bitrate.strip().lower()
    if valor.endswith("k"):
        return int(float(valor[:-1]))
    if valor.endswith("m"):
        return int(float(valor[:-1]) * 1000)
    return int(float(valor))


# ---------------------------------------------------------------------------
# Escada de qualidade — Passo 4 do guia individual:
#   360p (~400kbps), 480p, 720p (~2500kbps), 1080p (~5000kbps)
# O valor de 480p (~800kbps) segue a prática comum de escadas ABR.
# ---------------------------------------------------------------------------
RES_360P = Resolucao(nome="360p", altura=360, video_bitrate="400k", audio_bitrate="64k")
RES_480P = Resolucao(nome="480p", altura=480, video_bitrate="800k", audio_bitrate="96k")
RES_720P = Resolucao(nome="720p", altura=720, video_bitrate="2500k", audio_bitrate="128k")
RES_1080P = Resolucao(nome="1080p", altura=1080, video_bitrate="5000k", audio_bitrate="192k")

LADDER_COMPLETO: list[Resolucao] = [RES_360P, RES_480P, RES_720P, RES_1080P]


def ladder() -> list[Resolucao]:
    """Escada de qualidade em ordem crescente de altura (da pior pra melhor)."""
    return sorted(LADDER_COMPLETO, key=lambda r: r.altura)


# ---------------------------------------------------------------------------
# Caminhos e parâmetros de saída
# ---------------------------------------------------------------------------
# Padrões ancorados na raiz do repositório (derivada deste arquivo), para que
# worker Celery e API escrevam/leiam os MESMOS caminhos mesmo quando um deles
# é iniciado de outro diretório. UPLOAD_DIR usa o mesmo nome de variável do
# app/upload/router.py do Rafael, sem configurar nada duas vezes.
RAIZ_PROJETO = Path(__file__).resolve().parents[2]
BASE_UPLOADS = Path(os.getenv("UPLOAD_DIR", RAIZ_PROJETO / "uploads"))  # entrada  (contrato com Rafael)
BASE_VIDEOS = Path(os.getenv("VIDEOS_DIR", RAIZ_PROJETO / "videos"))    # saída    (contrato com Pedro)

HLS_SEGMENT_SECONDS = int(os.getenv("HLS_SEGMENT_SECONDS", "4"))
FFMPEG_PRESET = os.getenv("FFMPEG_PRESET", "veryfast")

NOME_PLAYLIST = "playlist.m3u8"      # contrato #1: /videos/{id}/{res}/playlist.m3u8
NOME_MASTER = "master.m3u8"          # contrato #1: /videos/{id}/master.m3u8
NOME_THUMBNAIL = "thumbnail.jpg"     # Passo 5 do guia
PADRAO_SEGMENTO = "segment_%03d.ts"  # segmentos HLS
