"""
Testes do carregador de .env (app/__init__.py).

Regras que protegem o time:
  * variável já definida no ambiente (Docker/CI/export) NUNCA é sobrescrita;
  * comentários e linhas vazias são ignorados; aspas externas removidas;
  * arquivo ausente é um no-op (não quebra quem não tem .env).
"""
from __future__ import annotations

import os

from app import carregar_dotenv


def test_carrega_chaves_do_arquivo(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "# comentário\nCHAVE_A3_TESTE=valor\nCHAVE_A3_ASPAS='entre aspas'\n\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("CHAVE_A3_TESTE", raising=False)
    monkeypatch.delenv("CHAVE_A3_ASPAS", raising=False)

    carregadas = carregar_dotenv(env)

    assert carregadas == 2
    assert os.environ["CHAVE_A3_TESTE"] == "valor"
    assert os.environ["CHAVE_A3_ASPAS"] == "entre aspas"


def test_nao_sobrescreve_variavel_ja_definida_no_ambiente(tmp_path, monkeypatch):
    """Docker/CI/export manual continuam vencendo o arquivo .env."""
    env = tmp_path / ".env"
    env.write_text("CHAVE_A3_EXISTENTE=do_arquivo\n", encoding="utf-8")
    monkeypatch.setenv("CHAVE_A3_EXISTENTE", "do_ambiente")

    carregar_dotenv(env)

    assert os.environ["CHAVE_A3_EXISTENTE"] == "do_ambiente"


def test_arquivo_inexistente_e_no_op(tmp_path):
    assert carregar_dotenv(tmp_path / "nao-existe.env") == 0
