"""
Testes de PAPEIS e 1º acesso (migração 002) — app/auth/tests/test_papeis.py.

Regras sob teste (decisões do usuário):
  * cadastro público cria SEMPRE estudante — e NÃO aceita papel do cliente
    (tentativa de injeção de role:"admin" no payload é ignorada);
  * /login e /sessao devolvem role e deve_trocar_senha;
  * conta de 1º acesso (deve_trocar_senha=true) é BLOQUEADA nas rotas
    protegidas até trocar a senha — e POST /trocar-senha é a rota que
    desbloqueia (é a única autenticada que aceita a conta nesse estado);
  * troca de senha reutiliza as regras de força do cadastro (trivial → 422)
    e exige a senha atual (401 quando errada).

O banco é o fake compartilhado (app/tests/fakes.py) — o fluxo de login é o
REAL (POST /login → cookie HttpOnly no TestClient).
"""
from __future__ import annotations

import pytest

from app.tests.fakes import (
    SENHA_PADRAO,
    banco_novo,
    cliente_novo,
    inserir_usuario,
    logar,
    trocar_get_client,
    usuario_com_sessao,
)

MODULO = "app.auth.usuarios"


@pytest.fixture()
def banco(monkeypatch):
    banco = banco_novo()
    trocar_get_client(monkeypatch, banco, MODULO)
    return banco


# ---------------------------------------------------------------------------
# Cadastro público: papel fixo, sem injeção
# ---------------------------------------------------------------------------

def test_cadastro_cria_estudante_por_padrao(banco):
    client = cliente_novo()
    resposta = client.post(
        "/cadastro",
        json={"nome": "Ana Aluna", "email": "ana@exemplo.com", "senha": SENHA_PADRAO},
    )

    assert resposta.status_code == 201
    usuario = resposta.json()["usuario"]
    assert usuario["role"] == "estudante"
    assert usuario["primeiro_acesso"] is False


def test_cadastro_ignora_papel_vindo_do_cliente(banco):
    """Ataque clássico: mandar role:"admin" no corpo do cadastro público.

    O router nem repassa o campo — o papel é gravado pelo servidor. O
    Pydantic ignora campo extra e o store grava 'estudante' fixo.
    """
    client = cliente_novo()
    resposta = client.post(
        "/cadastro",
        json={
            "nome": "Mallory",
            "email": "mallory@exemplo.com",
            "senha": SENHA_PADRAO,
            "role": "admin",
            "deve_trocar_senha": False,
        },
    )

    assert resposta.status_code == 201
    usuario = resposta.json()["usuario"]
    assert usuario["role"] == "estudante"

    linha = banco["usuarios"][-1]  # o que o banco REALMENTE gravou
    assert linha["role"] == "estudante"


def test_login_e_sessao_devolvem_papel(banco):
    registro, client = usuario_com_sessao(
        banco, role="professor", nome="Prof. Paulo", email="paulo@escola.com"
    )

    resposta = client.get("/sessao")
    assert resposta.status_code == 200
    usuario = resposta.json()["usuario"]
    assert usuario["role"] == "professor"
    assert usuario["usuario_id"] == registro["usuario_id"]


# ---------------------------------------------------------------------------
# 1º acesso: bloqueio até trocar a senha
# ---------------------------------------------------------------------------

@pytest.fixture()
def professor_bloqueado(banco):
    return usuario_com_sessao(
        banco,
        role="professor",
        nome="Prof. Novo",
        email="novo@escola.com",
        deve_trocar_senha=True,
    )


def test_login_da_conta_nova_marca_o_bloqueio(banco):
    inserir_usuario(
        banco,
        nome="Prof. Novo",
        email="novo@escola.com",
        role="professor",
        deve_trocar_senha=True,
    )
    client = cliente_novo()
    resposta = client.post(
        "/login",
        json={"email": "novo@escola.com", "senha": SENHA_PADRAO},
    )
    # O login EM SI funciona (credencial certa) — é a flag que trava as rotas.
    assert resposta.status_code == 200
    assert resposta.json()["usuario"]["primeiro_acesso"] is True


def test_conta_bloqueada_nao_usa_rota_protegida(professor_bloqueado):
    _, client = professor_bloqueado

    resposta = client.get("/admin/usuarios")
    assert resposta.status_code == 403
    assert "Primeiro acesso" in resposta.json()["detail"]
    assert "trocar" in resposta.json()["detail"].lower()


def test_primeiro_acesso_fluxo_completo(professor_bloqueado, banco):
    """Criação → bloqueio → troca → liberado (é o roteiro do professor novo)."""
    _, client = professor_bloqueado

    # 1. bloqueado na rota protegida (o guard `autenticado` vira ANTES do
    #    papel — para o 1º acesso o papel nem importa)...
    assert client.get("/admin/usuarios").status_code == 403

    # 2. ...mas a rota de troca aceita a conta bloqueada (é para isso que
    #    ela existe) — senha atual errada NÃO troca...
    errada = client.post(
        "/trocar-senha",
        json={"senha_atual": "senha-errada-123", "nova_senha": "NovaSenha#2026"},
    )
    assert errada.status_code == 401

    # 3. ...senha nova trivial é rejeitada com as MESMAS regras do cadastro...
    trivial = client.post(
        "/trocar-senha",
        json={"senha_atual": SENHA_PADRAO, "nova_senha": "senha123"},
    )
    assert trivial.status_code == 422

    # 4. ...a troca certa desbloqueia na hora...
    troca = client.post(
        "/trocar-senha",
        json={"senha_atual": SENHA_PADRAO, "nova_senha": "NovaSenha#2026"},
    )
    assert troca.status_code == 200
    usuario = troca.json()["usuario"]
    assert usuario["primeiro_acesso"] is False
    assert usuario["role"] == "professor"

    # ...o banco gravou o flag DESLIGADO...
    linha = next(
        r for r in banco["usuarios"] if r["email"] == "novo@escola.com"
    )
    assert linha["deve_trocar_senha"] is False

    # ...e a MESMA sessão já passa pelo guard `autenticado` (sem relogin) —
    # e agora o PAPEL fala: professor continua fora do painel do admin.
    resposta = client.get("/admin/usuarios")
    assert resposta.status_code == 403
    assert "Primeiro acesso" not in resposta.json()["detail"]


def test_admin_do_seed_fluxo_de_primeiro_acesso(banco):
    """O admin da migração 002 nasce com deve_trocar_senha=true.

    Mesmo o ADMIN: 1º acesso bloqueado → troca → painel liberado. É a
    comprovação de que o bloqueio vale para todos os papéis — e reproduz
    exatamente a conta do seed (admin@edustream.com).
    """
    _, client = usuario_com_sessao(
        banco,
        role="admin",
        nome="Administrador",
        email="admin@edustream.com",
        deve_trocar_senha=True,
    )
    assert client.get("/admin/usuarios").status_code == 403

    troca = client.post(
        "/trocar-senha",
        json={"senha_atual": SENHA_PADRAO, "nova_senha": "NovaSenha#2026"},
    )
    assert troca.status_code == 200
    assert client.get("/admin/usuarios").status_code == 200


def test_senha_trocada_vale_no_proximo_login(banco):
    _, client = usuario_com_sessao(
        banco,
        role="professor",
        nome="Prof. Troca",
        email="troca@escola.com",
        deve_trocar_senha=True,
    )
    resposta = client.post(
        "/trocar-senha",
        json={"senha_atual": SENHA_PADRAO, "nova_senha": "NovaSenha#2026"},
    )
    assert resposta.status_code == 200

    # Credencial VELHA não entra mais (mesma mensagem anti-enumeração).
    velha = cliente_novo()
    assert (
        velha.post(
            "/login", json={"email": "troca@escola.com", "senha": SENHA_PADRAO}
        ).status_code
        == 401
    )
    # Credencial NOVA entra.
    nova = cliente_novo()
    assert (
        nova.post(
            "/login", json={"email": "troca@escola.com", "senha": "NovaSenha#2026"}
        ).status_code
        == 200
    )


def test_trocar_senha_sem_sessao_responde_401(banco):
    client = cliente_novo()
    resposta = client.post(
        "/trocar-senha",
        json={"senha_atual": "qualquer-coisa", "nova_senha": "NovaSenha#2026"},
    )
    assert resposta.status_code == 401
