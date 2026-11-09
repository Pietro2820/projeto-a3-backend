"""
Pacote da aplicação.

Efeito colateral proposital no import: carrega o arquivo `.env` da raiz do
projeto (se existir) para o `os.environ`, SEM sobrescrever variáveis já
definidas (Docker/compose/CI/export manual continuam vencendo).

Por que aqui: em dev local (Opção B do README) ninguém exporta variável na
mão no Windows — o `.env` preenchido com as credenciais do Supabase passa a
valer assim que qualquer módulo do pacote `app` é importado (uvicorn, celery
e pytest inclusive). Em produção o compose já injeta via `env_file`, e este
loader vira um no-op.
"""
from __future__ import annotations

import os
from pathlib import Path


def carregar_dotenv(caminho: Path | None = None) -> int:
    """Lê um .env simples (CHAVE=valor) e devolve quantas variáveis entraram.

    Regras: linhas vazias/comentário são ignoradas; aspas externas removidas;
    variável já presente no ambiente NÃO é sobrescrevida (setdefault).
    """
    arquivo = Path(caminho) if caminho else Path(".env")
    if not arquivo.is_file():
        return 0

    carregadas = 0
    for linha in arquivo.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, _, valor = linha.partition("=")
        chave = chave.strip()
        valor = valor.strip().strip('"').strip("'")
        if not chave:
            continue
        if chave not in os.environ:
            os.environ[chave] = valor
            carregadas += 1
    return carregadas


carregar_dotenv()
