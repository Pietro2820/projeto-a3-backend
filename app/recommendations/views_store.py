"""
Contador de visualizações e histórico do usuário — em Redis.

Por que Redis e não Supabase?
  * O projeto já sobe Redis no docker-compose (é o broker do Celery), então não
    adiciona infraestrutura nova.
  * Contagem de view é escrita frequente e de baixo valor unitário — exatamente
    o caso de uso de um contador em memória.
  * Evita migração de banco: não existe tabela `visualizacoes` no Supabase e o
    `/trending` precisa funcionar no dia da apresentação.

Segue o mesmo padrão do `app/queue/status_store.py` (mesmo prefixo de chave,
mesmo cliente, mesmas funções pequenas) para o time não aprender duas APIs.

Chaves usadas:
    video_views:ranking        ZSET  video_id -> quantidade de views
    video_views:total:{id}     STR   contador individual
    video_views:historico:{user}  LIST  video_ids assistidos, mais recente 1º
"""
from __future__ import annotations

import os

import redis

VIEWS_REDIS_URL = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")

_redis_client = redis.Redis.from_url(VIEWS_REDIS_URL, decode_responses=True)

RANKING_KEY = "video_views:ranking"
TOTAL_KEY_PREFIX = "video_views:total:"
HISTORICO_KEY_PREFIX = "video_views:historico:"

# Limite do histórico por usuário: suficiente para recomendar sem crescer sem fim.
TAMANHO_HISTORICO = 20


def registrar_visualizacao(video_id: str, user_id: str | None = None) -> int:
    """Conta +1 view no vídeo e, se houver usuário, guarda no histórico dele.

    Returns:
        O novo total de visualizações do vídeo.
    """
    total = _redis_client.incr(f"{TOTAL_KEY_PREFIX}{video_id}")
    _redis_client.zincrby(RANKING_KEY, 1, video_id)

    if user_id:
        chave = f"{HISTORICO_KEY_PREFIX}{user_id}"
        # Remove antes de inserir: assistir de novo não pode duplicar no
        # histórico, senão o mesmo vídeo domina as recomendações do usuário.
        _redis_client.lrem(chave, 0, video_id)
        _redis_client.lpush(chave, video_id)
        _redis_client.ltrim(chave, 0, TAMANHO_HISTORICO - 1)

    return int(total)


def contar_visualizacoes(video_id: str) -> int:
    """Total de views de um vídeo (0 se nunca foi assistido)."""
    valor = _redis_client.get(f"{TOTAL_KEY_PREFIX}{video_id}")
    return int(valor) if valor else 0


def mais_assistidos(limite: int = 10) -> list[tuple[str, int]]:
    """Ranking de vídeos por visualizações, do mais assistido para o menos.

    Returns:
        Lista de tuplas `(video_id, views)`.
    """
    if limite <= 0:
        return []

    brutos = _redis_client.zrevrange(RANKING_KEY, 0, limite - 1, withscores=True)
    return [(video_id, int(pontuacao)) for video_id, pontuacao in brutos]


def historico_do_usuario(user_id: str) -> list[str]:
    """video_ids assistidos pelo usuário, do mais recente para o mais antigo."""
    return _redis_client.lrange(f"{HISTORICO_KEY_PREFIX}{user_id}", 0, -1)
