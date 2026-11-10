"""
Testes do painel admin — app/admin/tests/test_router.py.

Decisões sob teste: SOMENTE o papel admin cria professores e lista usuários;
professor e estudante levam 403 (mesmo autenticados), sem sessão leva 401.
A conta de professor nasce com deve_trocar_senha=true (1º acesso forçado) e
a lista de usuários NUNCA expõe senha_hash.

A visão geral (GET /admin/resumo) e o catálogo de moderação (GET
/admin/videos) têm o mesmo guard: os números agregados e o dono de cada
vídeo são privilégio de admin — professor/estudante não vê nem totais.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

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


@pytest.fixture()
def banco_completo(monkeypatch):
    """Banco falso apontado para TODOS os namespaces do /admin/resumo.

    O resumo lê três fontes (usuarios, videos, dias_estudo) e cada uma
    importa get_client para o próprio namespace — o monkeypatch precisa
    alcançar todas, senão uma delas cai no Supabase/local de verdade.
    """
    banco = banco_novo()
    trocar_get_client(
        monkeypatch,
        banco,
        MODULO,
        "app.database.videos",
        "app.streak.repo",
    )
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


# ---------------------------------------------------------------------------
# GET /admin/resumo — visão geral (só admin)
# ---------------------------------------------------------------------------

def _semear_plataforma(banco: dict) -> TestClient:
    """1 admin, 1 professor, 2 estudantes; 3 vídeos (2 prontos) e dias.

    Devolve o TestClient já logado como admin — a rota que o teste vai
    exercer exige este papel e o cookie de sessão fica no client.
    """
    _, client = usuario_com_sessao(
        banco, role="admin", nome="Admin", email="admin@edustream.com"
    )
    professor, _ = usuario_com_sessao(
        banco, role="professor", nome="Prof. Ana", email="ana@escola.com"
    )
    aluno1, _ = usuario_com_sessao(
        banco, role="estudante", nome="Aluno 1", email="aluno1@escola.com"
    )
    usuario_com_sessao(
        banco, role="estudante", nome="Aluno 2", email="aluno2@escola.com"
    )

    banco["videos"] += [
        {
            "video_id": "v-pronto-1",
            "titulo": "Aula 1",
            "status": "completed",
            "professor_id": professor["usuario_id"],
            "criado_em": "2026-01-01T00:00:00+00:00",
        },
        {
            "video_id": "v-pronto-2",
            "titulo": "Aula 2",
            "status": "completed",
            "professor_id": professor["usuario_id"],
            "criado_em": "2026-01-02T00:00:00+00:00",
        },
        {
            "video_id": "v-pendente",
            "titulo": "Aula 3",
            "status": "pending",
            "professor_id": professor["usuario_id"],
            "criado_em": "2026-01-03T00:00:00+00:00",
        },
    ]
    # 3 dias registrados, mas só 2 alunos distintos (aluno1 em dois dias).
    banco["dias_estudo"] += [
        {"usuario_id": aluno1["usuario_id"], "dia": "2026-06-01"},
        {"usuario_id": aluno1["usuario_id"], "dia": "2026-06-02"},
        {"usuario_id": aluno2_id(banco), "dia": "2026-06-01"},
    ]
    return client


def aluno2_id(banco: dict) -> str:
    return next(
        r["usuario_id"] for r in banco["usuarios"] if r["email"] == "aluno2@escola.com"
    )


def test_admin_ve_resumo_da_plataforma(banco_completo):
    client = _semear_plataforma(banco_completo)

    resposta = client.get("/admin/resumo")

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["usuarios"]["total"] == 4
    assert corpo["usuarios"]["admins"] == 1
    assert corpo["usuarios"]["professores"] == 1
    assert corpo["usuarios"]["estudantes"] == 2
    assert corpo["videos"]["total"] == 3
    assert corpo["videos"]["prontos"] == 2
    assert corpo["videos"]["pendentes"] == 1
    assert corpo["videos"]["processando"] == 0
    assert corpo["estudos"] == {"dias_registrados": 3, "alunos_ativos": 2}


def test_resumo_sem_dados_nao_quebra(banco_completo):
    """Plataforma recém-nascida: totais 0 e estudos com zeros — não 500."""
    _, client = usuario_com_sessao(
        banco_completo, role="admin", nome="Admin", email="admin@edustream.com"
    )

    resposta = client.get("/admin/resumo")

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["usuarios"] == {
        "total": 1, "admins": 1, "professores": 0, "estudantes": 0,
    }
    assert corpo["videos"]["total"] == 0
    assert corpo["estudos"] == {"dias_registrados": 0, "alunos_ativos": 0}


def test_resumo_estudos_nulo_sem_tabela(banco_completo, monkeypatch):
    """dias_estudo só existe com Supabase: falhando, sai null — não 500."""
    _, client = usuario_com_sessao(
        banco_completo, role="admin", nome="Admin", email="admin@edustream.com"
    )

    def _quebrar():
        raise RuntimeError("sem Supabase")

    monkeypatch.setattr("app.streak.repo.get_client", lambda *a, **k: _quebrar())

    resposta = client.get("/admin/resumo")

    assert resposta.status_code == 200
    assert resposta.json()["estudos"] is None


def test_estudante_nao_ve_resumo(banco_completo):
    _, client = usuario_com_sessao(
        banco_completo, role="estudante", nome="Aluno", email="aluno@escola.com"
    )
    assert client.get("/admin/resumo").status_code == 403


def test_professor_nao_ve_resumo(banco_completo):
    _, client = usuario_com_sessao(
        banco_completo, role="professor", nome="Prof.", email="prof@escola.com"
    )
    assert client.get("/admin/resumo").status_code == 403


def test_sem_sessao_nao_ve_resumo(banco_completo):
    assert cliente_novo().get("/admin/resumo").status_code == 401


# ---------------------------------------------------------------------------
# GET /admin/videos — catálogo completo para moderação (só admin)
# ---------------------------------------------------------------------------

def test_admin_ve_videos_com_dono(banco_completo):
    professor, _ = usuario_com_sessao(
        banco_completo, role="professor", nome="Prof. Ana", email="ana@escola.com"
    )
    usuario_com_sessao(
        banco_completo, role="admin", nome="Admin", email="admin@edustream.com"
    )
    sessao_admin = cliente_novo()
    logado = sessao_admin.post(
        "/login", json={"email": "admin@edustream.com", "senha": SENHA_PADRAO}
    )
    assert logado.status_code == 200

    banco_completo["videos"].append(
        {
            "video_id": "v-1",
            "titulo": "Aula da Ana",
            "status": "completed",
            "categoria": "Matemática",
            "professor_id": professor["usuario_id"],
            "criado_em": "2026-01-01T00:00:00+00:00",
        }
    )

    resposta = sessao_admin.get("/admin/videos")

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["total"] == 1
    video = corpo["videos"][0]
    assert video["titulo"] == "Aula da Ana"
    assert video["dono"]["nome"] == "Prof. Ana"
    assert video["dono"]["email"] == "ana@escola.com"
    assert "senha_hash" not in str(corpo)  # nada de hash em NENHUM campo
    assert "views" in video  # best-effort: 0 sem Redis, mas sempre presente


def test_video_sem_dono_conhecido_expoe_null(banco_completo):
    """professor_id apontando para conta apagada — o vídeo segue listado."""
    usuario_com_sessao(
        banco_completo, role="admin", nome="Admin", email="admin@edustream.com"
    )
    banco_completo["videos"].append(
        {
            "video_id": "v-orfao",
            "titulo": "Vídeo órfão",
            "status": "completed",
            "professor_id": "id-inexistente",
            "criado_em": "2026-01-01T00:00:00+00:00",
        }
    )

    sessao = cliente_novo()
    sessao.post(
        "/login", json={"email": "admin@edustream.com", "senha": SENHA_PADRAO}
    )

    resposta = sessao.get("/admin/videos")

    assert resposta.status_code == 200
    assert resposta.json()["videos"][0]["dono"] is None


def test_estudante_nao_ve_catalogo_admin(banco_completo):
    _, client = usuario_com_sessao(
        banco_completo, role="estudante", nome="Aluno", email="aluno@escola.com"
    )
    assert client.get("/admin/videos").status_code == 403


def test_professor_nao_ve_catalogo_admin(banco_completo):
    _, client = usuario_com_sessao(
        banco_completo, role="professor", nome="Prof.", email="prof@escola.com"
    )
    assert client.get("/admin/videos").status_code == 403
