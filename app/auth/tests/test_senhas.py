"""
Testes unitários de app/auth/senhas.py — PBKDF2-SHA256.

São o coração da segurança do login, então os testes cobrem o contrato todo:
formato do hash, roundtrip correto, senha errada, salt único por usuário e
comportamento seguro com entrada corrompida (nunca autenticar por acidente).
"""
from __future__ import annotations

import base64

import pytest

from app.auth.senhas import ITERACOES, gerar_hash, verificar_senha

SENHA = "senha12345"


# ---------------------------------------------------------------------------
# Formato do hash gerado
# ---------------------------------------------------------------------------

def test_hash_tem_quatro_partes_separadas_por_dolar():
    partes = gerar_hash(SENHA).split("$")

    assert len(partes) == 4
    assert partes[0] == "pbkdf2_sha256"
    assert int(partes[1]) == ITERACOES


def test_salt_e_hash_sao_base64_validos_com_tamanho_esperado():
    _, _, salt_b64, hash_b64 = gerar_hash(SENHA).split("$")

    assert len(base64.b64decode(salt_b64)) == 16
    assert len(base64.b64decode(hash_b64)) == 32


def test_nao_guarda_a_senha_em_texto_plano():
    hash_gerado = gerar_hash(SENHA)

    assert SENHA not in hash_gerado


# ---------------------------------------------------------------------------
# Roundtrip: gerar -> verificar
# ---------------------------------------------------------------------------

def test_senha_correta_verifica_com_sucesso():
    assert verificar_senha(SENHA, gerar_hash(SENHA)) is True


def test_senha_errada_rejeita():
    hash_gerado = gerar_hash(SENHA)

    assert verificar_senha("SenhaErrada999", hash_gerado) is False


def test_senha_vazia_rejeita_sem_explodir():
    hash_gerado = gerar_hash(SENHA)

    assert verificar_senha("", hash_gerado) is False


def test_hash_vazio_rejeita_sem_explodir():
    assert verificar_senha(SENHA, "") is False


# ---------------------------------------------------------------------------
# Salt único por usuário
# ---------------------------------------------------------------------------

def test_dois_hashes_da_mesma_senha_sao_diferentes():
    """Salt aleatório por usuário: mesma senha -> hashes diferentes no banco."""
    hash_1 = gerar_hash(SENHA)
    hash_2 = gerar_hash(SENHA)

    assert hash_1 != hash_2
    assert verificar_senha(SENHA, hash_1) is True
    assert verificar_senha(SENHA, hash_2) is True


# ---------------------------------------------------------------------------
# Entrada corrompida — nunca autenticar por acidente
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "hash_corrompido",
    [
        "nao-tem-formato",
        "pbkdf2_sha256$abc$salt$hash",          # iterações não numéricas
        "pbkdf2_sha256$120000$salt-!-ruim$hash",  # salt fora do base64
        "sha256$120000$AAAA$AAAA",               # algoritmo diferente
        "pbkdf2_sha256$0$AAAA$AAAA",             # iterações zeradas
    ],
)
def test_hash_invalido_rejeita_sem_explodir(hash_corrompido):
    assert verificar_senha(SENHA, hash_corrompido) is False


def test_none_rejeita_sem_explodir():
    assert verificar_senha(SENHA, None) is False
