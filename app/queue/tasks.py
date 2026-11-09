import logging

from app.database.videos import atualizar_status
from app.queue.celery_app import celery_app
from app.queue.status_store import set_status
from app.transcoding import processar  # módulo do João

logger = logging.getLogger(__name__)


def _sincronizar_banco(video_id: str, status: str) -> None:
    """Reflete o status no banco (Supabase ou fallback local).

    O Redis alimenta o /status em tempo real, mas o CATÁLOGO (e portanto as
    recomendações, o /trending e os cards do player) lê do banco filtrando
    status='completed'. Sem esta sincronização o vídeo transcodificava, o
    /status dizia "completed", mas ele nunca aparecia no frontend.

    Falha aqui não pode derrubar a task: loga e segue (o Redis já tem o status).
    """
    try:
        atualizar_status(video_id, status)
    except Exception as exc:
        logger.warning(
            "Não foi possível atualizar o status de %s para '%s' no banco: %s",
            video_id, status, exc,
        )


@celery_app.task(name="app.queue.tasks.hello_world")
def hello_world():
    """Task de exemplo pra validar que Celery + Redis estão funcionando."""
    return "pong"


@celery_app.task(
    name="app.queue.tasks.transcodificar_video",
    bind=True,
    max_retries=3,
    default_retry_delay=60,  # 1 min na primeira tentativa
)
def transcodificar_video(self, video_id: str, caminho_original: str):
    """
    Contrato combinado com o João:
    entrada -> video_id, caminho do arquivo original
    saida   -> status (completed/failed) + caminho dos arquivos HLS gerados

    Importante: processar() do João NUNCA levanta exceção — ela sempre volta
    com {"status": "completed", ...} ou {"status": "failed", "error": ...}.
    Por isso o retry no caso "failed" precisa ser disparado explicitamente
    aqui embaixo, e não só confiar no except.
    """
    backoff = [60, 300, 900]  # 1min, 5min, 15min

    try:
        resultado = processar(video_id, caminho_original)
    except Exception as exc:
        # Rede de segurança: erro inesperado FORA do contrato do João
        # (ex: import quebrado, bug no adapter). Na prática não deveria
        # acontecer, já que processar() trata tudo internamente.
        if self.request.retries >= self.max_retries:
            set_status(video_id, "failed", {"erro": str(exc)})
            _sincronizar_banco(video_id, "failed")
            raise
        atraso = backoff[min(self.request.retries, len(backoff) - 1)]
        raise self.retry(exc=exc, countdown=atraso)

    if resultado["status"] == "failed":
        set_status(video_id, "failed", {"erro": resultado["error"]})
        _sincronizar_banco(video_id, "failed")
        if self.request.retries >= self.max_retries:
            # Esgotou as tentativas: não faz mais sentido levantar retry,
            # deixa o status "failed" registrado e encerra a task normalmente.
            return resultado
        atraso = backoff[min(self.request.retries, len(backoff) - 1)]
        raise self.retry(
            exc=RuntimeError(resultado["error"]),
            countdown=atraso,
        )

    # status == "completed"
    set_status(video_id, "completed", {"caminho_hls": resultado["caminho_hls"]})
    _sincronizar_banco(video_id, "completed")
    return resultado


@celery_app.task(name="app.queue.tasks.processar_upload")
def processar_upload(video_id: str, caminho_arquivo: str):
    """Dispara a etapa de transcodificação após validar o upload."""
    set_status(video_id, "processing")
    _sincronizar_banco(video_id, "processing")
    transcodificar_video.delay(video_id, caminho_arquivo)
    return {"video_id": video_id, "status": "processing"}


@celery_app.task(name="app.queue.tasks.notificar_status")
def notificar_status(video_id: str, status: str):
    """Placeholder para notificações (ex: WebSocket, e-mail) quando o status muda."""
    return {"video_id": video_id, "status": status}