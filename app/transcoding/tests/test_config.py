"""
Testes da configuração da escada de qualidade.

Não precisam de FFmpeg — validam "no papel" os números do Passo 4 do guia:
360p (~400kbps), 480p, 720p (~2500kbps), 1080p (~5000kbps).
"""
from app.transcoding.config import (
    RES_360P,
    RES_480P,
    RES_720P,
    RES_1080P,
    ladder,
)


def test_ladder_tem_as_quatro_resolucoes_do_guia():
    nomes = [r.nome for r in ladder()]
    assert nomes == ["360p", "480p", "720p", "1080p"]


def test_ladder_vem_em_ordem_crescente_de_altura():
    alturas = [r.altura for r in ladder()]
    assert alturas == sorted(alturas)


def test_bitrates_seguem_o_guia_individual():
    assert RES_360P.video_bitrate == "400k"     # guia: 360p ~400kbps
    assert RES_720P.video_bitrate == "2500k"    # guia: 720p ~2500kbps
    assert RES_1080P.video_bitrate == "5000k"   # guia: 1080p ~5000kbps
    assert RES_480P.altura == 480


def test_bandwidth_soma_video_e_audio_em_bps():
    # 400k de vídeo + 64k de áudio = 464.000 bps
    assert RES_360P.bandwidth == 464_000
    # 2500k + 128k = 2.628.000 bps
    assert RES_720P.bandwidth == 2_628_000


def test_bandwidth_cresce_junto_com_a_resolucao():
    bandwidths = [r.bandwidth for r in ladder()]
    assert bandwidths == sorted(bandwidths)
    assert all(b > 0 for b in bandwidths)
