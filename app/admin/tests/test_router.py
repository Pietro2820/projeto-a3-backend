"""
Testes do painel admin — app/admin/tests/test_router.py.

Decisões sob teste: SOMENTE o papel admin cria professores e lista usuários;
professor e estudante levam 403 (mesmo autenticados), sem sessão leva 401.
A conta de professor nasce com deve_trocar_senha=true (1º acesso forçado) e
a lista de usuários NUNCA expõe senha_hash.
"""
from __future__ import annotations

import pytest

from app.tests.fakes import (
    SENHA_PADRAO,
    banco_novo,
    cliente_novo,
    trocar_get_client,
    usuario_com_sessao,
)

MODULO = "app.auth.usuarios"

DADOS_PROFESSOR = {
    "nome": "Prof. Ana",
    "email": "ana.prof@escola.com",
    "senha_temporaria": "Temporaria#2026",
}


@pytest.fixture()
def banco(monkeypatch):
    banco = banco_novo()
    trocar_get_client(monkeypatch, banco, MODULO)
    return banco


# ---------------------------------------------------------------------------
# POST /admin/professores — só admin
# ---------------------------------------------------------------------------

def test_admin_cria_professor(banco):
    _, client = usuario_com_sessao(
        banco, role="admin", nome="Admin", email="admin@edustream.com"
    )

    resposta = client.post("/admin/professores", json=DADOS_PROFESSOR)

    assert resposta.status_code == 201
    professor = resposta.json()["usuario"]
    assert professor["role"] == "professor"
    assert professor["primeiro_acesso"] is True
    assert "senha_hash" not in professor

    linha = next(
        r for r in banco["usuarios"]
        if r["email"] == "ana.prof@escola.com"
    )
    assert linha["role"] == "professor"
    assert linha["deve_trocar_senha"] is True


def test_estudante_nao_cria_professor(banco):
    _, client = usuario_com_sessao(
        banco, role="estudante", nome="Aluno", email="aluno@escola.com"
    )

    resposta = client.post("/admin/professores", json=DADOS_PROFESSOR)

    assert resposta.status_code == 403
    assert all(
        r["email"] != DADOS_PROFESSOR["email"] for r in banco["usuarios"]
    )


def test_professor_nao_cria_professor(banco):
    """Auto-promoção entre pares também é 403 — papel vem do banco."""
    _, client = usuario_com_sessao(
        banco, role="professor", nome="Prof. Bia", email="bia@escola.com"
    )

    resposta = client.post(
        "/admin/professores",
        json={**DADOS_PROFESSOR, "email": "colega@escola.com"},
    )

    assert resposta.status_code == 403
    assert all(r["email"] != "colega@escola.com" for r in banco["usuarios"])


def test_sem_sessao_responde_401(banco):
    resposta = cliente_novo().post("/admin/professores", json=DADOS_PROFESSOR)
    assert resposta.status_code == 401


def test_email_duplicado_responde_409(banco):
    _, client = usuario_com_sessao(
        banco, role="admin", nome="Admin", email="admin@edustream.com"
    )
    # O admin do seed já está na base — criar professor com o mesmo e-mail
    # é o caminho natural de duplicidade.
    resposta = client.post(
        "/admin/professores",
        json={
            "nome": "Duplicado",
            "email": "admin@edustream.com",
            "senha_temporaria": "Temporaria#2026",
        },
    )
    assert resposta.status_code == 409


def test_senha_temporaria_trivial_responde_422(banco):
    _, client = usuario_com_sessao(
        banco, role="admin", nome="Admin", email="admin@edustream.com"
    )
    resposta = client.post(
        "/admin/professores",
        json={
            "nome": "Prof. Fraco",
            "email": "fraco@escola.com",
            "senha_temporaria": "12345678",  # está na lista de triviais
        },
    )
    assert resposta.status_code == 422


def test_conta_criada_loga_e_esta_bloqueada(banco):
    """O fluxo ponta a ponta combinado com o usuário (admin → professor)."""
    _, client = usuario_com_sessao(
        banco, role="admin", nome="Admin", email="admin@edustream.com"
    )
    assert client.post("/admin/professores", json=DADOS_PROFESSOR).status_code == 201

    # Professor loga com a senha temporária...
    sessao = cliente_novo()
    logado = sessao.post(
        "/login",
        json={"email": "ana.prof@escola.com", "senha": "Temporaria#2026"},
    )
    assert logado.status_code == 200
    assert logado.json()["usuario"]["primeiro_acesso"] is True

    # ...e está bloqueado nas rotas protegidas até trocar.
    assert sessao.get("/admin/usuarios").status_code == 403


# ---------------------------------------------------------------------------
# GET /admin/usuarios — só admin, sem senha_hash
# ---------------------------------------------------------------------------

def test_admin_lista_usuarios_sem_hash(banco):
    usuario_com_sessao(
        banco, role="admin", nome="Admin", email="admin@edustream.com"
    )
    usuario_com_sessao(
        banco, role="estudante", nome="Aluno", email="aluno@escola.com"
    )

    sessao = cliente_novo()
    sessao.post(
        "/login", json={"email": "admin@edustream.com", "senha": SENHA_PADRAO}
    )

    resposta = sessao.get("/admin/usuarios")
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["total"] >= 2
    for usuario in corpo["usuarios"]:
        assert "senha_hash" not in usuario  # projeção do store + fake fiel
        assert {"usuario_id", "nome", "email", "role"} <= set(usuario)


def test_estudante_nao_lista_usuarios(banco):
    _, client = usuario_com_sessao(
        banco, role="estudante", nome="Aluno", email="aluno@escola.com"
    )
    assert client.get("/admin/usuarios").status_code == 403


def test_professor_nao_lista_usuarios(banco):
    _, client = usuario_com_sessao(
        banco, role="professor", nome="Prof.", email="prof@escola.com"
    )
    assert client.get("/admin/usuarios").status_code == 403
