"""
Rate limiting do login — em memória, sem dependência nova.

Bloqueio por IP e por e-mail: após N falhas dentro da janela, a chave fica
bloqueada pelo período de bloqueio e TODA tentativa recebe 429 com mensagem
genérica (não revela se o e-mail existe — mesma disciplina do 401).

Por que em memória: a API roda em um processo uvicorn só (escopo da etapa).
Quando o projeto escalar para várias instâncias, o lugar natural é o Redis
que a fila de transcodificação já usa — trocar a estrutura por trás desta
classe, sem mexer nas rotas.

Limites configuráveis por variável de ambiente (lidos a cada uso, então
monkeypatch.setenv funciona nos testes):

  TENTATIVAS_LOGIN       padrão 5
  JANELA_LOGIN_MINUTOS   padrão 15  (janela que conta as falhas)
  BLOQUEIO_LOGIN_MINUTOS padrão 15  (duração do bloqueio ao estourar)
"""
from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque


def _env_int(nome: str, padrao: int) -> int:
    try:
        return int(os.getenv(nome, str(padrao)))
    except (TypeError, ValueError):
        return padrao


class LimitadorLogin:
    """Falhas por chave (`email:x@y.com` / `ip:1.2.3.4`) em janela deslizante."""

    def __init__(self) -> None:
        self._falhas: dict[str, deque[float]] = defaultdict(deque)
        self._bloqueado_ate: dict[str, float] = {}
        self._lock = threading.Lock()

    # -- leitura ------------------------------------------------------------

    def bloqueado(self, chave: str) -> float:
        """Segundos restantes de bloqueio (0.0 = livre)."""
        agora = time.monotonic()
        with self._lock:
            ate = self._bloqueado_ate.get(chave, 0.0)
            if ate <= agora:
                self._bloqueado_ate.pop(chave, None)
                return 0.0
            return ate - agora

    def limite_de_tentativas(self) -> int:
        return max(1, _env_int("TENTATIVAS_LOGIN", 5))

    # -- escrita ------------------------------------------------------------

    def registrar_falha(self, chave: str) -> None:
        """Conta uma falha; ao atingir o limite dentro da janela, bloqueia."""
        agora = time.monotonic()
        janela = max(1, _env_int("JANELA_LOGIN_MINUTOS", 15)) * 60
        bloqueio = max(1, _env_int("BLOQUEIO_LOGIN_MINUTOS", 15)) * 60

        with self._lock:
            fila = self._falhas[chave]
            fila.append(agora)
            while fila and agora - fila[0] > janela:
                fila.popleft()
            if len(fila) >= self.limite_de_tentativas():
                self._bloqueado_ate[chave] = agora + bloqueio
                fila.clear()  # bloqueio acabou, orçamento recomeça do zero

    def resetar(self, chave: str) -> None:
        """Sucesso no login limpa o histórico do E-MAIL (o do IP fica — um
        atacante não pode zerar o contador dele acertando uma conta própria)."""
        with self._lock:
            self._falhas.pop(chave, None)
            self._bloqueado_ate.pop(chave, None)

    def limpar_tudo(self) -> None:
        """Para testes e administração manual."""
        with self._lock:
            self._falhas.clear()
            self._bloqueado_ate.clear()


# Instância única do processo — as rotas de login usam esta.
limitador_login = LimitadorLogin()
