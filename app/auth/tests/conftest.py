"""
Fixtures compartilhadas dos testes de autenticação.

O rate limit do login é um estado GLOBAL do processo (dict em memória, de
propósito). Sem reset, os 401s que os testes de comportamento produzem
acumulariam entre testes e disparariam o bloqueio de 429 em quem roda
depois — este fixture autouse devolve o limitador ao zero antes de CADA
teste, para toda a suíte (antiga e nova).
"""
from __future__ import annotations

import pytest

from app.auth.rate_limit import limitador_login


@pytest.fixture(autouse=True)
def _rate_limit_zerado():
    limitador_login.limpar_tudo()
    yield
    limitador_login.limpar_tudo()
