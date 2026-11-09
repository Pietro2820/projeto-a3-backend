"""
Testes do serviço de transcodificação (Passo 3 do guia — TDD).

Cobrem, nesta ordem:
    1. ambiente (FFmpeg instalado, inspeção de mídia)
    2. conversão para UMA resolução (Passo 2a) + aspect ratio
    3. geração de HLS válido (Passo 2c)
    4. todas as resoluções + master playlist (Passo 4)
    5. thumbnail do meio do vídeo (Passo 5)
    6. erros e arquivos inválidos (critério "tratamento de erros" do edital)
    7. compatibilidade com os formatos aceitos no upload (contrato do Rafael)
"""
from pathlib import Path

import pytest
import subprocess

from app.transcoding import ffmpeg_service
from app.transcoding.config import RES_360P, RES_720P, ladder
from app.transcoding.errors import TranscodingError, VideoInvalidoError

# Sem FFmpeg no sistema, o módulo inteiro é pulado (não "quebra" o CI de quem
# ainda não instalou) — a mensagem aponta para o README com instruções.
pytestmark = pytest.mark.skipif(
    not ffmpeg_service.ffmpeg_disponivel(),
    reason="FFmpeg não instalado (instruções em app/transcoding/README.md)",
)


# ---------------------------------------------------------------------------
# 1. Ambiente e inspeção de mídia
# ---------------------------------------------------------------------------
def test_ffmpeg_instalado():
    assert ffmpeg_service.ffmpeg_disponivel(), "ffmpeg e ffprobe precisam estar no PATH"


def test_duracao_video(video_teste):
    duracao = ffmpeg_service.duracao_video(video_teste)
    assert duracao == pytest.approx(6.0, abs=0.6)


def test_dimensoes_video(video_teste):
    assert ffmpeg_service.dimensoes_video(video_teste) == (1280, 720)


def test_duracao_video_arquivo_inexistente_levanta_erro(tmp_path):
    with pytest.raises(VideoInvalidoError):
        ffmpeg_service.duracao_video(tmp_path / "nao_existe.mp4")


# ---------------------------------------------------------------------------
# 2. Passo 2a — conversão para UMA resolução (começar simples!)
# ---------------------------------------------------------------------------
def test_converte_video_para_resolucao(video_teste, diretorio_saida):
    """Nome sugerido no guia individual (Passo 3)."""
    saida = ffmpeg_service.converter_para_resolucao(
        video_teste, diretorio_saida / "video_720p.mp4", RES_720P
    )
    assert saida.is_file()
    assert saida.stat().st_size > 0
    assert ffmpeg_service.dimensoes_video(saida) == (1280, 720)
    assert ffmpeg_service.duracao_video(saida) == pytest.approx(6.0, abs=0.6)


def test_conversao_mantem_aspect_ratio_original(video_teste, diretorio_saida):
    """Passo 4 do guia: manter o aspect ratio original."""
    saida = ffmpeg_service.converter_para_resolucao(
        video_teste, diretorio_saida / "video_360p.mp4", RES_360P
    )
    largura, altura = ffmpeg_service.dimensoes_video(saida)
    assert altura == 360
    assert largura % 2 == 0, "largura precisa ser par (exigência do H.264)"
    assert largura / altura == pytest.approx(1280 / 720, rel=0.02)


def test_conversao_falha_com_arquivo_inexistente(tmp_path, diretorio_saida):
    with pytest.raises(VideoInvalidoError):
        ffmpeg_service.converter_para_resolucao(
            tmp_path / "fantasma.mp4", diretorio_saida / "saida.mp4", RES_720P
        )


def test_arquivo_que_nao_e_video_falha_com_erro_claro(tmp_path, diretorio_saida):
    """Arquivo com extensão .mp4 mas conteúdo de texto -> erro tratado, não crash."""
    falso_video = tmp_path / "original.mp4"
    falso_video.write_text("isso definitivamente não é um vídeo", encoding="utf-8")
    with pytest.raises(TranscodingError):
        ffmpeg_service.converter_para_resolucao(
            falso_video, diretorio_saida / "saida.mp4", RES_360P
        )


# ---------------------------------------------------------------------------
# 3. Passo 2c — HLS (.m3u8 + .ts)
# ---------------------------------------------------------------------------
def test_gera_hls_valido(hls_360p):
    """Nome sugerido no guia individual (Passo 3)."""
    playlist = hls_360p
    assert playlist.name == "playlist.m3u8"
    assert playlist.is_file()

    conteudo = playlist.read_text(encoding="utf-8")
    assert conteudo.startswith("#EXTM3U")
    assert "#EXT-X-PLAYLIST-TYPE:VOD" in conteudo
    assert "#EXTINF:" in conteudo
    assert conteudo.rstrip().endswith("#EXT-X-ENDLIST")

    segmentos = sorted(playlist.parent.glob("segment_*.ts"))
    assert len(segmentos) >= 2, "vídeo de 6s com segmentos de 4s deve gerar >= 2 .ts"
    assert all(s.stat().st_size > 0 for s in segmentos)


def test_hls_tem_a_dimensao_da_resolucao_pedida(hls_360p):
    largura, altura = ffmpeg_service.dimensoes_video(hls_360p)
    assert (largura, altura) == (640, 360)


def test_hls_soma_dos_segmentos_cobre_a_duracao_do_video(hls_360p):
    linhas = hls_360p.read_text(encoding="utf-8").splitlines()
    # formato da linha: "#EXTINF:4.000000," (vírgula no fim é opcional)
    duracoes = [
        float(linha.split(":", 1)[1].rstrip(","))
        for linha in linhas
        if linha.startswith("#EXTINF:")
    ]
    assert duracoes, "playlist precisa de pelo menos um #EXTINF"
    assert sum(duracoes) == pytest.approx(6.0, abs=1.0)


# ---------------------------------------------------------------------------
# 4. Passo 4 — todas as resoluções + estrutura do contrato + master playlist
# ---------------------------------------------------------------------------
def test_gera_todas_as_resolucoes(transcodificacao_completa):
    """Nome sugerido no guia individual (Passo 3)."""
    resultado = transcodificacao_completa
    assert resultado["status"] == "completed"

    for res in ladder():
        info = resultado["playlists"][res.nome]
        playlist = Path(info["playlist"])
        assert playlist.is_file(), f"playlist ausente para {res.nome}"
        assert info["altura"] == res.altura
        assert info["largura"] % 2 == 0

    alturas = [resultado["playlists"][r.nome]["altura"] for r in ladder()]
    assert alturas == [360, 480, 720, 1080]


def test_estrutura_de_pastas_segundo_contrato(transcodificacao_completa):
    """Contrato #1 da doc técnica: videos/{id}/{res}/playlist.m3u8 + master.m3u8."""
    resultado = transcodificacao_completa
    raiz = Path(resultado["diretorio"])

    assert raiz.name == "video-fixture"
    assert Path(resultado["master_playlist"]) == raiz / "master.m3u8"
    assert Path(resultado["playlists"]["720p"]["playlist"]) == raiz / "720p" / "playlist.m3u8"
    assert Path(resultado["thumbnail"]) == raiz / "thumbnail.jpg"


def test_gera_master_playlist_valido(transcodificacao_completa):
    master = Path(transcodificacao_completa["master_playlist"])
    assert master.is_file()

    linhas = master.read_text(encoding="utf-8").splitlines()
    assert linhas[0] == "#EXTM3U"

    stream_infs = [l for l in linhas if l.startswith("#EXT-X-STREAM-INF:")]
    assert len(stream_infs) == 4, "master precisa listar as 4 variantes"
    for inf in stream_infs:
        assert "BANDWIDTH=" in inf
        assert "RESOLUTION=" in inf

    # Cada referência do master precisa existir em disco (caminho relativo).
    referencias = [
        linhas[i + 1] for i, l in enumerate(linhas) if l.startswith("#EXT-X-STREAM-INF:")
    ]
    assert referencias == ["360p/playlist.m3u8", "480p/playlist.m3u8",
                           "720p/playlist.m3u8", "1080p/playlist.m3u8"]
    for ref in referencias:
        assert (master.parent / ref).is_file(), f"variante referenciada não existe: {ref}"

    bandwidths = [int(l.split("BANDWIDTH=")[1].split(",")[0]) for l in stream_infs]
    assert bandwidths == sorted(bandwidths), "variantes devem ir da menor pra maior banda"


def test_caminho_padrao_de_saida_usa_videos_e_video_id(video_teste, tmp_path, monkeypatch):
    """Sem diretorio_saida explícito, deve cair em VIDEOS_DIR/{video_id} (contrato)."""
    monkeypatch.setattr(ffmpeg_service, "BASE_VIDEOS", tmp_path / "videos")
    resultado = ffmpeg_service.transcodificar(
        "abc-123", video_teste, resolucoes=[RES_360P], gerar_thumb=False
    )
    esperado = (tmp_path / "videos" / "abc-123").as_posix()
    assert resultado["diretorio"] == esperado
    assert Path(resultado["master_playlist"]).is_file()


def test_transcodificar_falha_com_video_inexistente(tmp_path):
    with pytest.raises(VideoInvalidoError):
        ffmpeg_service.transcodificar(
            "id-qualquer", tmp_path / "nada_aqui.mp4", diretorio_saida=tmp_path / "out"
        )


# ---------------------------------------------------------------------------
# 5. Passo 5 — thumbnail
# ---------------------------------------------------------------------------
def test_extrai_thumbnail(video_teste, diretorio_saida):
    """Nome sugerido no guia individual (Passo 3)."""
    destino = diretorio_saida / "thumbnail.jpg"
    thumb = ffmpeg_service.extrair_thumbnail(video_teste, destino)

    assert thumb.is_file()
    assert thumb.stat().st_size > 0
    assert thumb.read_bytes()[:3] == b"\xff\xd8\xff", "assinatura JPEG inválida"
    assert ffmpeg_service.dimensoes_video(thumb) == (1280, 720)


def test_thumbnail_usa_instante_do_meio_do_video():
    assert ffmpeg_service.instante_do_meio(30.0) == 15.0
    assert ffmpeg_service.instante_do_meio(0.0) == 0.0
    assert ffmpeg_service.instante_do_meio(-4.0) == 0.0  # nunca seek negativo


def test_transcodificacao_completa_inclui_thumbnail(transcodificacao_completa):
    thumb = Path(transcodificacao_completa["thumbnail"])
    assert thumb.name == "thumbnail.jpg"
    assert thumb.is_file()


# ---------------------------------------------------------------------------
# 7. Compatibilidade com os formatos aceitos no upload (Rafael)
#    app/upload/router.py: ALLOWED_EXTENSIONS = {".mp4", ".avi", ".mov", ".webm"}
#    A transcodificação precisa engolir todos eles.
# ---------------------------------------------------------------------------
def _gerar_fonte_no_container(formato: str, destino: Path) -> Path:
    """Vídeo sintético curto (4s) no container pedido, com áudio."""
    codecs = {
        "mp4": ["-c:v", "libx264", "-c:a", "aac"],
        "mov": ["-c:v", "libx264", "-c:a", "aac"],
        "avi": ["-c:v", "mpeg4", "-c:a", "pcm_s16le"],
        "webm": ["-c:v", "libvpx", "-c:a", "libvorbis"],
    }[formato]
    destino.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24:duration=4",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
            *codecs, "-shortest",
            str(destino),
        ],
        check=True,
        capture_output=True,
    )
    return destino


@pytest.mark.parametrize("formato", ["mp4", "avi", "mov", "webm"])
def test_converte_todos_os_formatos_aceitos_no_upload(formato, tmp_path, diretorio_saida):
    fonte = _gerar_fonte_no_container(formato, tmp_path / f"original.{formato}")
    saida = ffmpeg_service.converter_para_resolucao(
        fonte, diretorio_saida / f"saida_{formato}.mp4", RES_360P
    )
    assert saida.is_file()
    assert saida.stat().st_size > 0
    _, altura = ffmpeg_service.dimensoes_video(saida)
    assert altura == 360
