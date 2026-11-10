"""
Sessões do EduStream — token opaco em cookie HttpOnly.

Modelo: o POST /login gera um token aleatório (secrets.token_urlsafe, 256
bits de entropia), guarda `token → usuario_id + expiração` EM MEMÓRIA e o
devolve num cookie HttpOnly. JavaScript NÃO consegue ler o cookie (à prova
de XSS roubar o token) — o front descobre a sessão via GET /sessao.

Escolhas conscientes:

  * Token OPACO (não JWT): nada é decodificável no cliente, revogar é apagar
    a entrada do dicionário (POST /logout) — sem biblioteca nova.
  * Em memória como o rate limit: reiniciar a API derruba as sessões (aceito
    no escopo da etapa; a evolução natural é o Redis da fila).
  * Secure do cookie é CONDICIONAL ao ambiente: em http://localhost fica
    desligado (dev), sob HTTPS liga (e por trás de proxy respeita
    X-Forwarded-Proto). Configurável na emissão (app/auth/router.py).
  * Validade configurável por variável de ambiente:
      SESSAO_TTL_HORAS  padrão 12 — cookie de sessão (some ao fechar o
                        navegador); o token expira junto no servidor;
      LEMBRAR_DIAS      padrão 30 — "Lembrar de mim" marcado: cookie com
                        Max-Age e token com a MESMA validade no servidor.
"""
from __future__ import annotations

import os
import secrets
import threading
from datetime import datetime, timedelta, timezone

# Nome do cookie. De propósito NÃO contém "senha" — o teste de contrato
# ("senha" jamais aparece num corpo de resposta) continua simples de explicar.
CHAVE_COOKIE = "edustream_sessao"

_sessoes: dict[str, dict] = {}
_lock = threading.Lock()


def _env_int(nome: str, padrao: int) -> int:
    try:
        return int(os.getenv(nome, str(padrao)))
    except (TypeError, ValueError):
        return padrao


def _limpar_expiradas(agora: datetime) -> None:
    # Oportunista: roda dentro de criar/resolver, sem tarefa de fundo.
    expiradas = [t for t, s in _sessoes.items() if s["expira_em"] <= agora]
    for t in expiradas:
        _sessoes.pop(t, None)


def criar_sessao(usuario_id: str, lembrar: bool = False) -> tuple[str, timedelta]:
    """Gera token novo e devolve (token, validade) — cookie usa o MESMO ttl."""
    ttl = (
        timedelta(days=max(1, _env_int("LEMBRAR_DIAS", 30)))
        if lembrar
        else timedelta(hours=max(1, _env_int("SESSAO_TTL_HORAS", 12)))
    )
    token = secrets.token_urlsafe(32)
    agora = datetime.now(timezone.utc)

    with _lock:
        _limpar_expiradas(agora)
        _sessoes[token] = {
            "usuario_id": usuario_id,
            "expira_em": agora + ttl,
        }
    return token, ttl


def resolver_sessao(token: str | None) -> str | None:
    """usuario_id da sessão válida, ou None (inexistente/expirada)."""
    if not token:
        return None
    agora = datetime.now(timezone.utc)

    with _lock:
        sessao = _sessoes.get(token)
        if not sessao or sessao["expira_em"] <= agora:
            _sessoes.pop(token, None)
            return None
        return sessao["usuario_id"]


def encerrar_sessao(token: str | None) -> None:
    """Logout: revoga no servidor (o cookie vira lixo inútil no navegador)."""
    if not token:
        return
    with _lock:
        _sessoes.pop(token, None)
