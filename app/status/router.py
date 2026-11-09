"""
Rota de status do processamento (contrato #5).

    GET /status/{video_id} -> pending | processing | completed | failed

Duas fontes, em ordem de prioridade:
  1. Redis (`status_store`) — gravado pelas tasks do Celery em tempo real;
  2. Banco (Supabase ou fallback local) — usado quando o Redis não tem o
     vídeo ou está fora do ar.

Antes, qualquer falha de Redis virava 500 na cara do frontend; e um vídeo
recém-enviado (fila ainda sem worker) devolvia 404 mesmo existindo no banco.
O fallback mantém o player informado em qualquer cenário.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.database.videos import buscar_video
from app.queue.status_store import get_status

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/status/{video_id}")
def consultar_status(video_id: str):
    status = None
    try:
        status = get_status(video_id)
    except Exception as exc:  # Redis fora do ar não pode derrubar a rota
        logger.warning("Redis indisponível ao consultar status de %s: %s", video_id, exc)

    if status is not None:
        return status

    # Fallback: o banco conhece o vídeo (o upload grava status='pending' lá,
    # e a task atualiza para completed/failed quando o processamento acaba).
    try:
        video = buscar_video(video_id)
    except Exception as exc:
        logger.warning("Banco indisponível ao consultar vídeo %s: %s", video_id, exc)
        video = None

    if video is not None:
        return {
            "video_id": video_id,
            "status": video.get("status", "pending"),
            "origem": "banco",
        }

    raise HTTPException(status_code=404, detail="video_id não encontrado")
