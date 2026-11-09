"""
Banco local em JSON — fallback de desenvolvimento SEM Supabase.

Por que existe:
    O time ainda não criou o projeto no Supabase (veja o README). Sem
    credenciais, `get_client()` levanta RuntimeError e TODAS as rotas que
    tocam o banco devolviam 503 — ou seja, o frontend nunca saía do modo de
    demonstração e a "conversa" entre back e front não acontecia.

    Com este módulo, `app/database/videos.py` cai automaticamente num banco
    JSON local (padrão: `data/videos.json`) quando o Supabase não está
    configurado. A API inteira volta a funcionar de ponta a ponta em dev:
    upload → fila → transcodificação → status → recomendações → player.

    Assim que SUPABASE_URL/SUPABASE_KEY aparecerem no .env, o Supabase volta
    a ser usado automaticamente — nenhuma outra linha precisa mudar.

Detalhes de implementação:
    * Arquivo único, lista de dicts (o catálogo de dev é pequeno).
    * Lock de processo + escrita atômica (tmp → rename) para o worker Celery
      e a API poderem escrever sem corromper o arquivo.
    * O caminho é lido do ambiente a cada chamada (facilita testes com
      monkeypatch.setenv).
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Raiz do repositório: API e worker Celery precisam ler/escrever o MESMO
# banco mesmo quando iniciados de diretórios diferentes.
RAIZ_PROJETO = Path(__file__).resolve().parents[2]
CAMINHO_PADRAO = RAIZ_PROJETO / "data" / "videos.json"

_lock = threading.Lock()


def caminho_do_banco() -> Path:
    return Path(os.getenv("LOCAL_DB_PATH", str(CAMINHO_PADRAO)))


def _ler() -> list[dict]:
    """Lê o catálogo; arquivo ausente ou corrompido vira lista vazia."""
    caminho = caminho_do_banco()
    if not caminho.is_file():
        return []
    try:
        conteudo = json.loads(caminho.read_text(encoding="utf-8"))
        return conteudo if isinstance(conteudo, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _escrever(videos: list[dict]) -> None:
    """Escrita atômica: grava num temporário e renomeia no lugar."""
    caminho = caminho_do_banco()
    caminho.parent.mkdir(parents=True, exist_ok=True)
    temporario = caminho.with_suffix(".tmp")
    temporario.write_text(
        json.dumps(videos, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporario.replace(caminho)


def inserir(video: dict) -> dict:
    """Insere um registro completo (formato do contrato #2) e o devolve."""
    with _lock:
        videos = _ler()
        registro = dict(video)
        registro.setdefault("video_id", str(uuid.uuid4()))
        registro.setdefault("status", "pending")
        registro.setdefault(
            "criado_em", datetime.now(timezone.utc).isoformat()
        )
        videos.append(registro)
        _escrever(videos)
        return registro


def buscar(video_id: str) -> dict | None:
    """Busca pelo video_id; None se não existir."""
    for video in _ler():
        if video.get("video_id") == video_id:
            return dict(video)
    return None


def atualizar_status(video_id: str, status: str) -> dict | None:
    """Atualiza o status de um vídeo. None se ele não existir (sem erro)."""
    with _lock:
        videos = _ler()
        encontrado = None
        for video in videos:
            if video.get("video_id") == video_id:
                video["status"] = status
                encontrado = video
        if encontrado is None:
            return None
        _escrever(videos)
        return dict(encontrado)


def listar(status: str | None = None) -> list[dict]:
    """Lista vídeos, opcionalmente filtrando por status."""
    videos = _ler()
    if status:
        videos = [video for video in videos if video.get("status") == status]
    return [dict(video) for video in videos]
