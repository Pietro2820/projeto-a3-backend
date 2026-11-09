"""
Acesso aos metadados de vídeos — Supabase com fallback local automático.

Cada função tenta primeiro o Supabase (produção). Se as credenciais não
estiverem configuradas (`get_client()` levanta RuntimeError), cai no banco
JSON local de `app/database/local_store.py` — assim o projeto inteiro roda
em desenvolvimento sem depender de serviço externo, e a integração
backend ↔ frontend pode ser testada de ponta a ponta.

Quem chama estas funções NÃO precisa saber qual backend está em uso: a
assinatura e o formato de retorno (contrato #2) são os mesmos nos dois casos.
"""
from __future__ import annotations

import logging

from app.database import local_store
from app.database.client import get_client

logger = logging.getLogger(__name__)

TABLE = "videos"

_aviso_dado = False


def _cliente_supabase():
    """Devolve o client do Supabase ou None quando ele não está configurado.

    O aviso é logado uma única vez para não poluir os logs (as rotas de
    recomendação chamam `buscar_video` em loop).
    """
    global _aviso_dado
    try:
        return get_client()
    except RuntimeError as exc:
        if not _aviso_dado:
            logger.warning(
                "Supabase não configurado — usando banco local em %s. (%s)",
                local_store.caminho_do_banco(),
                exc,
            )
            _aviso_dado = True
        return None


def criar_video(titulo: str, descricao: str = None, tags: str = None,
                categoria: str = None, autor: str = None) -> dict:
    """
    Insere um novo registro na tabela videos.
    status e criado_em usam os valores default definidos no banco
    (status='pending', criado_em=now()).
    Retorna o registro criado (incluindo o video_id gerado).
    """
    payload = {
        "titulo": titulo,
        "descricao": descricao,
        "tags": tags,
        "categoria": categoria,
        "autor": autor,
    }

    client = _cliente_supabase()
    if client is None:
        return local_store.inserir(payload)

    response = client.table(TABLE).insert(payload).execute()
    return response.data[0]


def inserir_video(metadata: dict) -> dict:
    """
    Insere um registro COMPLETO (video_id, criado_em e status já definidos
    por quem chama — é o caso do módulo de upload, que gera o UUID).
    """
    client = _cliente_supabase()
    if client is None:
        return local_store.inserir(metadata)

    response = client.table(TABLE).insert(metadata).execute()
    return response.data[0]


def buscar_video(video_id: str) -> dict | None:
    """
    Busca um vídeo pelo video_id. Retorna None se não encontrar.
    """
    client = _cliente_supabase()
    if client is None:
        return local_store.buscar(video_id)

    response = client.table(TABLE).select("*").eq("video_id", video_id).execute()
    if not response.data:
        return None
    return response.data[0]


def atualizar_status(video_id: str, status: str) -> dict | None:
    """
    Atualiza o status de um vídeo (pending, processing, completed, failed).
    Usada pelas tasks do Celery para refletir o progresso do processamento.
    """
    client = _cliente_supabase()
    if client is None:
        return local_store.atualizar_status(video_id, status)

    response = (
        client.table(TABLE)
        .update({"status": status})
        .eq("video_id", video_id)
        .execute()
    )
    # update em video_id inexistente volta vazio: None em vez de IndexError
    # (a task do Celery só loga o aviso, não quebra por causa disso).
    return response.data[0] if response.data else None


def listar_videos(status: str = None) -> list[dict]:
    """
    Lista vídeos, opcionalmente filtrando por status.
    """
    client = _cliente_supabase()
    if client is None:
        return local_store.listar(status)

    query = client.table(TABLE).select("*")
    if status:
        query = query.eq("status", status)
    response = query.execute()
    return response.data
