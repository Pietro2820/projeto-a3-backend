"""
Testes unitários do cliente Supabase (`app/database/client.py`).

O edital pede "tratamento de erros na aplicação". Este módulo existe só para
isso: em vez de deixar o `supabase-py` falhar com uma mensagem críptica quando
faltam credenciais, ele levanta um RuntimeError explicando o que fazer.

Esses testes fixam essa mensagem — se alguém "refatorar" o texto para algo
vago, o teste quebra.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.database import client


@pytest.fixture
def sem_credenciais(monkeypatch):
    """Simula o cenário real do projeto hoje: .env ainda sem Supabase."""
    monkeypatch.setattr(client, "SUPABASE_URL", None)
    monkeypatch.setattr(client, "SUPABASE_KEY", None)


@pytest.fixture
def com_credenciais(monkeypatch):
    monkeypatch.setattr(client, "SUPABASE_URL", "https://abc.supabase.co")
    monkeypatch.setattr(client, "SUPABASE_KEY", "chave-secreta")


# ---------------------------------------------------------------------------
# Caminho de erro — credenciais ausentes
# ---------------------------------------------------------------------------

def test_sem_url_e_sem_key_levanta_runtime_error(sem_credenciais):
    with pytest.raises(RuntimeError):
        client.get_client()


def test_erro_explica_que_as_variaveis_estao_no_env(sem_credenciais):
    with pytest.raises(RuntimeError) as erro:
        client.get_client()

    assert "SUPABASE_URL" in str(erro.value)
    assert "SUPABASE_KEY" in str(erro.value)


def test_erro_aponta_a_solucao_copiar_o_env_example(sem_credenciais):
    """A mensagem precisa dizer o que fazer, não só o que deu errado."""
    with pytest.raises(RuntimeError) as erro:
        client.get_client()

    assert ".env.example" in str(erro.value)


@pytest.mark.parametrize(
    "url,key",
    [
        (None, "chave-secreta"),
        ("https://abc.supabase.co", None),
        ("", ""),
    ],
)
def test_credencial_parcial_tambem_levanta_erro(monkeypatch, url, key):
    """Configuração pela metade é o bug mais comum — precisa falhar cedo."""
    monkeypatch.setattr(client, "SUPABASE_URL", url)
    monkeypatch.setattr(client, "SUPABASE_KEY", key)

    with pytest.raises(RuntimeError):
        client.get_client()


# ---------------------------------------------------------------------------
# Caminho feliz
# ---------------------------------------------------------------------------

def test_com_credenciais_cria_o_client_do_supabase(com_credenciais):
    with patch.object(client, "create_client") as mock_create:
        client.get_client()

    mock_create.assert_called_once_with("https://abc.supabase.co", "chave-secreta")


def test_com_credenciais_devolve_o_client_criado(com_credenciais):
    sentinel = object()
    with patch.object(client, "create_client", return_value=sentinel):
        assert client.get_client() is sentinel
