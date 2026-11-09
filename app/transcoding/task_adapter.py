"""
Adaptador entre a fila Celery (Pietro) e o serviço de transcodificação.

Passo 7 do guia: "Ele expõe a fila, você fornece a função que ela vai chamar."

Esta função É a entrega do contrato #3 da documentação técnica:
    Entrada: video_id, caminho do arquivo original
    Saída:   status (completed / failed) + caminhos dos arquivos HLS gerados

O formato exato de entrada/saída está documentado em CONTRATO_TASK.md —
leve esse arquivo para a conversa com o Pietro.

Exemplo de como o Pietro pluga na task dele:

    from app.transcoding import executar_transcodificacao

    @celery_app.task(bind=True, name="transcodificar_video", max_retries=2)
    def task_transcodificacao(self, video_id: str, caminho_original: str):
        resultado = executar_transcodificacao(video_id, caminho_original)
        if resultado["status"] == "failed":
            # o erro já vem tratado; retry é opcional (a função é idempotente)
            raise self.retry(exc=RuntimeError(resultado["error"]), countdown=30)
        return resultado
"""
from __future__ import annotations

import logging
import os

from app.transcoding import ffmpeg_service
from app.transcoding.config import Resolucao
from app.transcoding.errors import TranscodingError
from app.transcoding.storage import VideoStorage

logger = logging.getLogger(__name__)


def s3_habilitado() -> bool:
    """Upload para S3/MinIO só acontece com ENABLE_S3=true (dev local roda sem)."""
    return os.getenv("ENABLE_S3", "").strip().lower() in {"1", "true", "yes", "sim"}


def executar_transcodificacao(
    video_id: str,
    caminho_original: str,
    resolucoes: list[Resolucao] | None = None,
    fazer_upload_s3: bool | None = None,
) -> dict:
    """Ponto de entrada chamado pela task Celery. NUNCA levanta exceção.

    Args:
        video_id: UUID do vídeo (contrato #1).
        caminho_original: caminho do arquivo original salvo no upload.
        resolucoes: opcional — subconjunto da escada (padrão: escada completa).
            Existe para dev/teste; em produção a task chama sem esse argumento.
        fazer_upload_s3: opcional — força/desliga o upload S3. Padrão: usa a
            variável de ambiente ENABLE_S3.

    Returns (contrato #3):
        Sucesso:
            {
              "status": "completed",
              "video_id": "...",
              "diretorio": "videos/{video_id}",
              "master_playlist": "videos/{video_id}/master.m3u8",
              "playlists": {"360p": {"playlist": ..., "largura": ..., "altura": ..., "bandwidth": ...}, ...},
              "thumbnail": "videos/{video_id}/thumbnail.jpg",
              "s3": None | {"bucket": ..., "original": ..., "processados": [...], "master_playlist_url": ...}
            }
        Falha:
            {"status": "failed", "video_id": "...", "error": "mensagem legível"}

    A função é idempotente: rodar de novo com o mesmo video_id sobrescreve os
    arquivos de saída (FFmpeg roda com -y) — seguro para retry do Celery.
    """
    if fazer_upload_s3 is None:
        fazer_upload_s3 = s3_habilitado()

    try:
        resultado = ffmpeg_service.transcodificar(
            video_id=video_id,
            caminho_original=caminho_original,
            resolucoes=resolucoes,
        )
    except TranscodingError as exc:
        logger.exception("Transcodificação falhou para video_id=%s", video_id)
        return {"status": "failed", "video_id": str(video_id), "error": str(exc)}
    except Exception as exc:  # erro inesperado também precisa respeitar o contrato
        logger.exception("Erro inesperado na transcodificação de video_id=%s", video_id)
        return {
            "status": "failed",
            "video_id": str(video_id),
            "error": f"Erro inesperado ({type(exc).__name__}): {exc}",
        }

    # Apelido no formato que a task do Pietro já esperava (placeholder em
    # app/queue/tasks.py usava "caminho_hls") — zero surpresa na integração.
    resultado["caminho_hls"] = resultado["master_playlist"]

    resultado["s3"] = None
    if fazer_upload_s3:
        try:
            storage = VideoStorage.from_env()
            chave_original = storage.upload_original(video_id, caminho_original)
            chaves_processados = storage.upload_processados(video_id, resultado["diretorio"])
            resultado["s3"] = {
                "bucket": storage.bucket,
                "original": chave_original,
                "processados": chaves_processados,
                "master_playlist_url": storage.url_publica(
                    f"videos/{video_id}/master.m3u8"
                ),
            }
        except TranscodingError as exc:
            logger.exception("Upload S3 falhou para video_id=%s", video_id)
            return {
                "status": "failed",
                "video_id": str(video_id),
                "error": f"Transcodificação local ok, mas o upload para S3/MinIO falhou: {exc}",
            }

    logger.info("Transcodificação concluída para video_id=%s", video_id)
    return resultado
