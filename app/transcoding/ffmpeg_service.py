"""
Serviço de transcodificação com FFmpeg (via subprocess).

Por que subprocess e não ffmpeg-python?
    A documentação técnica do projeto permite os dois ("FFmpeg (via
    ffmpeg-python / subprocess)"). Usamos subprocess direto porque é mais
    explícito, não adiciona dependência e deixa fácil logar/reproduzir o
    comando exato que o FFmpeg recebeu — o que ajuda muito a depurar.

Fluxo de desenvolvimento (Passo 2 do guia individual — começar SIMPLES):
    1. converter_para_resolucao()  -> .mp4 em UMA resolução só (ex: 720p)
    2. extrair_thumbnail()         -> thumbnail.jpg com o frame do meio
    3. gerar_hls()                 -> .m3u8 + .ts por resolução
    4. gerar_master_playlist()     -> master.m3u8 apontando as variantes
    5. transcodificar()            -> orquestra tudo seguindo o contrato #1
                                      da documentação técnica:
        videos/{video_id}/{resolucao}/playlist.m3u8
        videos/{video_id}/master.m3u8
        videos/{video_id}/thumbnail.jpg
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from app.transcoding.config import (
    BASE_VIDEOS,
    FFMPEG_PRESET,
    HLS_SEGMENT_SECONDS,
    NOME_MASTER,
    NOME_THUMBNAIL,
    PADRAO_SEGMENTO,
    Resolucao,
    ladder,
)
from app.transcoding.errors import (
    FFmpegNaoEncontradoError,
    TranscodingError,
    VideoInvalidoError,
)

# Argumentos comuns a todo comando ffmpeg: sem banner, sem estatísticas de
# progresso e log só de erros — em worker de fila, saída limpa importa.
_ARGS_SILENCIOSOS = ["-hide_banner", "-loglevel", "error", "-nostats"]


# ---------------------------------------------------------------------------
# Infra: localizar e executar FFmpeg/ffprobe
# ---------------------------------------------------------------------------
def ffmpeg_disponivel() -> bool:
    """True se ffmpeg E ffprobe estão instalados no PATH do sistema."""
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _binario(nome: str) -> str:
    caminho = shutil.which(nome)
    if caminho is None:
        raise FFmpegNaoEncontradoError(
            f"'{nome}' não encontrado no PATH. Instale o FFmpeg no sistema "
            "(não é lib Python): https://ffmpeg.org/download.html"
        )
    return caminho


def _executar(comando: list[str]) -> None:
    """Executa um comando ffmpeg; em caso de falha levanta erro com o stderr."""
    try:
        processo = subprocess.run(comando, capture_output=True, text=True, check=False)
    except OSError as exc:  # binário sumiu, sem permissão etc.
        raise TranscodingError(f"Falha ao executar '{comando[0]}': {exc}") from exc

    if processo.returncode != 0:
        cauda_erro = "\n".join((processo.stderr or "").strip().splitlines()[-5:])
        raise TranscodingError(
            f"FFmpeg falhou (código {processo.returncode}) no comando:\n"
            f"  {' '.join(comando)}\n"
            f"Últimas linhas do erro:\n{cauda_erro}"
        )


def _ffprobe_json(caminho: str | Path) -> dict:
    """Roda ffprobe e devolve o JSON com formato/streams do arquivo de mídia."""
    comando = [
        _binario("ffprobe"),
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(caminho),
    ]
    processo = subprocess.run(comando, capture_output=True, text=True, check=False)
    if processo.returncode != 0 or not processo.stdout.strip():
        raise VideoInvalidoError(
            f"ffprobe não conseguiu ler '{caminho}' "
            f"(arquivo ausente, corrompido ou não é mídia): "
            f"{(processo.stderr or '').strip()[:300]}"
        )
    try:
        return json.loads(processo.stdout)
    except json.JSONDecodeError as exc:
        raise TranscodingError(f"Saída do ffprobe não é JSON válido para '{caminho}'") from exc


def _validar_entrada(caminho: str | Path) -> Path:
    """Garante que o vídeo de entrada existe antes de gastar tempo com FFmpeg."""
    entrada = Path(caminho)
    if not entrada.is_file():
        raise VideoInvalidoError(f"Vídeo original não encontrado: '{entrada}'")
    return entrada


# ---------------------------------------------------------------------------
# Inspeção de mídia
# ---------------------------------------------------------------------------
def duracao_video(caminho: str | Path) -> float:
    """Duração do vídeo em segundos (usa o container; cai pro stream de vídeo)."""
    dados = _ffprobe_json(caminho)
    duracao = (dados.get("format") or {}).get("duration")
    if duracao is None:
        for stream in dados.get("streams", []):
            if stream.get("duration"):
                duracao = stream["duration"]
                break
    if duracao is None:
        raise TranscodingError(f"Não foi possível determinar a duração de '{caminho}'")
    return float(duracao)


def dimensoes_video(caminho: str | Path) -> tuple[int, int]:
    """(largura, altura) do stream de vídeo.

    Funciona para .mp4, .jpg, segmentos .ts e até playlists .m3u8 — se o
    ffprobe não abrir o .m3u8, tenta o primeiro segmento .ts ao lado.
    """
    caminho = Path(caminho)
    try:
        return _dimensoes_de(caminho)
    except (TranscodingError, VideoInvalidoError):
        if caminho.suffix == ".m3u8":
            segmento = next(iter(sorted(caminho.parent.glob("*.ts"))), None)
            if segmento is not None:
                return _dimensoes_de(segmento)
        raise


def _dimensoes_de(caminho: Path) -> tuple[int, int]:
    dados = _ffprobe_json(caminho)
    for stream in dados.get("streams", []):
        if stream.get("codec_type") == "video" and stream.get("width"):
            return int(stream["width"]), int(stream["height"])
    raise TranscodingError(f"Nenhum stream de vídeo encontrado em '{caminho}'")


# ---------------------------------------------------------------------------
# Passo 2a — conversão simples para UMA resolução
# ---------------------------------------------------------------------------
def converter_para_resolucao(
    entrada: str | Path,
    saida: str | Path,
    resolucao: Resolucao,
) -> Path:
    """Converte o vídeo para uma única resolução, mantendo o aspect ratio.

    Gera um .mp4 (H.264 + AAC) — é o primeiro degrau do Passo 2 do guia,
    antes de partir para HLS.

    Detalhes técnicos importantes:
        scale=-2:{altura}  -> largura calculada automaticamente, arredondada
                              para múltiplo de 2 (exigência do H.264),
                              preservando o aspect ratio original.
        -pix_fmt yuv420p   -> compatibilidade com navegadores.
        -movflags +faststart -> metadata no início do arquivo (web-friendly).
    """
    entrada = _validar_entrada(entrada)
    saida = Path(saida)
    saida.parent.mkdir(parents=True, exist_ok=True)

    _executar([
        _binario("ffmpeg"), *_ARGS_SILENCIOSOS, "-y",
        "-i", str(entrada),
        "-vf", f"scale=-2:{resolucao.altura}",
        "-c:v", "libx264", "-preset", FFMPEG_PRESET, "-b:v", resolucao.video_bitrate,
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", resolucao.audio_bitrate, "-ac", "2",
        "-movflags", "+faststart",
        str(saida),
    ])

    if not saida.is_file() or saida.stat().st_size == 0:
        raise TranscodingError(f"Conversão não gerou arquivo de saída válido: '{saida}'")
    return saida


# ---------------------------------------------------------------------------
# Passo 5 — thumbnail (frame do meio do vídeo)
# ---------------------------------------------------------------------------
def instante_do_meio(duracao_segundos: float) -> float:
    """Instante usado na thumbnail: exatamente o meio do vídeo (nunca negativo)."""
    return max(duracao_segundos / 2.0, 0.0)


def extrair_thumbnail(entrada: str | Path, destino: str | Path) -> Path:
    """Extrai um frame do MEIO do vídeo e salva como JPEG.

    O -ss ANTES do -i faz seek rápido (por keyframe); para thumbnail a
    precisão de décimos de segundo é suficiente.
    """
    entrada = _validar_entrada(entrada)
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)

    meio = instante_do_meio(duracao_video(entrada))
    _executar([
        _binario("ffmpeg"), *_ARGS_SILENCIOSOS, "-y",
        "-ss", f"{meio:.3f}",
        "-i", str(entrada),
        "-frames:v", "1",
        "-q:v", "2",
        str(destino),
    ])

    if not destino.is_file() or destino.stat().st_size == 0:
        raise TranscodingError(f"Thumbnail não foi gerada: '{destino}'")
    return destino


# ---------------------------------------------------------------------------
# Passo 2c — HLS por resolução (.m3u8 + .ts)
# ---------------------------------------------------------------------------
def gerar_hls(entrada: str | Path, diretorio: str | Path, resolucao: Resolucao) -> Path:
    """Gera o pacote HLS de UMA resolução: playlist.m3u8 + segmentos .ts.

    Detalhes técnicos importantes:
        -force_key_frames expr:gte(t,n_forced*SEG)
            força keyframe a cada SEG segundos — sem isso o FFmpeg só corta
            segmento onde já existe keyframe e os .ts ficam com duração torta.
        -hls_playlist_type vod
            marca a playlist como VOD (fecha com #EXT-X-ENDLIST e permite
            seek completo no player).
        -hls_time SEG
            duração alvo de cada segmento.
    """
    entrada = _validar_entrada(entrada)
    diretorio = Path(diretorio)
    diretorio.mkdir(parents=True, exist_ok=True)

    playlist = diretorio / "playlist.m3u8"
    padrao_segmento = diretorio / PADRAO_SEGMENTO

    _executar([
        _binario("ffmpeg"), *_ARGS_SILENCIOSOS, "-y",
        "-i", str(entrada),
        "-vf", f"scale=-2:{resolucao.altura}",
        "-c:v", "libx264", "-preset", FFMPEG_PRESET, "-b:v", resolucao.video_bitrate,
        "-pix_fmt", "yuv420p",
        "-force_key_frames", f"expr:gte(t,n_forced*{HLS_SEGMENT_SECONDS})",
        "-c:a", "aac", "-b:a", resolucao.audio_bitrate, "-ac", "2",
        "-f", "hls",
        "-hls_time", str(HLS_SEGMENT_SECONDS),
        "-hls_playlist_type", "vod",
        "-hls_segment_filename", str(padrao_segmento),
        str(playlist),
    ])

    if not playlist.is_file():
        raise TranscodingError(f"Geração de HLS não criou a playlist: '{playlist}'")
    return playlist


# ---------------------------------------------------------------------------
# Master playlist (multivariante)
# ---------------------------------------------------------------------------
def gerar_master_playlist(diretorio_video: str | Path, variantes: list[dict]) -> Path:
    """Escreve o master.m3u8 que aponta para as variantes de resolução.

    Args:
        diretorio_video: pasta do vídeo (ex: videos/{video_id}/).
        variantes: lista de dicts com as chaves:
            resolucao (Resolucao), largura (int), altura (int), bandwidth (int)

    O master usa caminhos RELATIVOS ({nome}/playlist.m3u8), então a pasta
    inteira pode ser servida de qualquer raiz (local, S3, MinIO, CDN) sem
    reescrever nada — importante pro player do Pedro.

    Formato de saída (exemplo):
        #EXTM3U
        #EXT-X-VERSION:3
        #EXT-X-STREAM-INF:BANDWIDTH=464000,RESOLUTION=640x360
        360p/playlist.m3u8
        ...
    """
    if not variantes:
        raise TranscodingError("Master playlist precisa de pelo menos uma variante")

    diretorio_video = Path(diretorio_video)
    diretorio_video.mkdir(parents=True, exist_ok=True)

    linhas = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-INDEPENDENT-SEGMENTS"]
    for variante in sorted(variantes, key=lambda v: (v["altura"], v["bandwidth"])):
        res: Resolucao = variante["resolucao"]
        linhas.append(
            f"#EXT-X-STREAM-INF:BANDWIDTH={variante['bandwidth']},"
            f"RESOLUTION={variante['largura']}x{variante['altura']},"
            f'NAME="{res.nome}"'
        )
        linhas.append(f"{res.nome}/playlist.m3u8")

    master = diretorio_video / NOME_MASTER
    master.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    return master


# ---------------------------------------------------------------------------
# Orquestração completa (o que a task Celery do Pietro chama)
# ---------------------------------------------------------------------------
def transcodificar(
    video_id: str,
    caminho_original: str | Path,
    diretorio_saida: str | Path | None = None,
    resolucoes: list[Resolucao] | None = None,
    gerar_thumb: bool = True,
) -> dict:
    """Pipeline completo: todas as resoluções + master.m3u8 + thumbnail.

    Segue o contrato #1 da documentação técnica:
        {saida}/{video_id}/{resolucao}/playlist.m3u8
        {saida}/{video_id}/master.m3u8
        {saida}/{video_id}/thumbnail.jpg

    Args:
        video_id: UUID do vídeo (gerado no upload pelo módulo do Rafael).
        caminho_original: caminho do arquivo original (ex: uploads/{id}/original.mp4).
        diretorio_saida: pasta de destino. Padrão: VIDEOS_DIR/{video_id} (contrato).
        resolucoes: subconjunto da escada (padrão: escada completa). Útil em
            testes e em dev para iterar rápido.
        gerar_thumb: se False, pula a thumbnail.

    Returns:
        dict com status "completed" e todos os caminhos gerados (ver
        CONTRATO_TASK.md para o formato exato).

    Raises:
        VideoInvalidoError: entrada ausente/ilegível.
        TranscodingError: qualquer falha do FFmpeg no meio do caminho.
    """
    entrada = _validar_entrada(caminho_original)
    escada = ladder() if resolucoes is None else sorted(resolucoes, key=lambda r: r.altura)
    if not escada:
        raise TranscodingError("Nenhuma resolução informada para transcodificar")

    destino = Path(diretorio_saida) if diretorio_saida else BASE_VIDEOS / str(video_id)
    destino.mkdir(parents=True, exist_ok=True)

    variantes: list[dict] = []
    playlists: dict[str, dict] = {}

    for res in escada:
        playlist = gerar_hls(entrada, destino / res.nome, res)
        # Mede a resolução REAL gerada (e não a calculada) para o master.m3u8.
        largura, altura = dimensoes_video(playlist)
        variantes.append({
            "resolucao": res,
            "largura": largura,
            "altura": altura,
            "bandwidth": res.bandwidth,
        })
        playlists[res.nome] = {
            "playlist": playlist.as_posix(),
            "largura": largura,
            "altura": altura,
            "bandwidth": res.bandwidth,
        }

    master = gerar_master_playlist(destino, variantes)

    thumbnail = None
    if gerar_thumb:
        thumbnail = extrair_thumbnail(entrada, destino / NOME_THUMBNAIL)

    return {
        "status": "completed",
        "video_id": str(video_id),
        "diretorio": destino.as_posix(),
        "master_playlist": master.as_posix(),
        "playlists": playlists,
        "thumbnail": thumbnail.as_posix() if thumbnail else None,
    }
