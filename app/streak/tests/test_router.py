"""
Testes das ROTAS do streak — app/streak/tests/test_router.py.

O que está sob teste (decisões do usuário):
  * GET /streak/meu: recurso EXCLUSIVO do estudante (professor/admin → 403,
    sem sessão → 401); o cálculo é 100% servidor;
  * POST /watch com concluido=true grava o dia de estudo — só com SESSÃO,
    só para estudante, só para vídeo que EXISTE; idempotente no mesmo dia;
  * user_id do corpo é IGNORADO quando há sessão (cookie vence — ninguém
    assiste "como outra pessoa", nem forja dia para outro usuário);
  * falha de streak NUNCA derruba o /watch.

Redis (views) e Supabase são dublês; a cadeia de sessão é a real.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.recommendations import views_store
from app.streak import calculo
from app.tests.fakes import (
    banco_novo,
    inserir_usuario,
    trocar_get_client,
    usuario_com_sessao,
)

MODULOS = ("app.auth.usuarios", "app.database.videos", "app.streak.repo")

VIDEO_ID = "dddddddd-4444-4444-4444-444444444444"


class ViewsFalsas:
    """Contador em memória — guarda quem assistiu para as assertivas."""

    def __init__(self):
        self.registros = []

    def registrar_visualizacao(self, video_id, user_id):
        self.registros.append((video_id, user_id))
        return len(self.registros)


@pytest.fixture()
def ambiente(monkeypatch):
    banco = banco_novo()
    trocar_get_client(monkeypatch, banco, *MODULOS)

    # Vídeo real no banco (a conclusão exige vídeo existente).
    banco["videos"].append({
        "video_id": VIDEO_ID,
        "titulo": "Aula 1",
        "professor_id": "cccccccc-3333-3333-3333-333333333333",
        "status": "completed",
        "criado_em": "2026-01-01T00:00:00+00:00",
        "tags": [],
    })

    views = ViewsFalsas()
    monkeypatch.setattr(
        views_store, "registrar_visualizacao", views.registrar_visualizacao
    )

    estudante, client = usuario_com_sessao(
        banco, role="estudante", nome="Aluno", email="aluno@escola.com"
    )
    return banco, views, estudante, client


# ---------------------------------------------------------------------------
# POST /watch — conclusão registra o dia (só estudante com sessão)
# ---------------------------------------------------------------------------

def test_conclusao_grava_o_dia_de_estudo(ambiente):
    banco, views, estudante, client = ambiente
    resposta = client.post("/watch", json={"video_id": VIDEO_ID, "concluido": True})

    assert resposta.status_code == 200
    assert views.registros == [(VIDEO_ID, estudante["usuario_id"])]
    dias = {d["dia"] for d in banco["dias_estudo"]}
    assert dias == {calculo.dia_de_hoje().isoformat()}
    assert banco["dias_estudo"][0]["usuario_id"] == estudante["usuario_id"]


def test_conclusao_e_idempotente_no_mesmo_dia(ambiente):
    """Concluir 3 vídeos no mesmo dia conta UM dia (upsert ignore_duplicates)."""
    banco, _, _, client = ambiente
    for _ in range(3):
        client.post("/watch", json={"video_id": VIDEO_ID, "concluido": True})

    assert len(banco["dias_estudo"]) == 1


def test_sem_sessao_nao_grava_dia(ambiente):
    banco, _, _, _ = ambiente
    resposta = TestClient(app).post(
        "/watch", json={"video_id": VIDEO_ID, "concluido": True}
    )
    assert resposta.status_code == 200  # a view anônima continua OK...
    assert banco["dias_estudo"] == []   # ...mas nada de dia de estudo


def test_professor_nao_tem_dia_de_estudo(ambiente):
    banco, views, _, _ = ambiente
    _, client = usuario_com_sessao(
        banco, role="professor", nome="Prof.", email="prof@escola.com"
    )
    resposta = client.post("/watch", json={"video_id": VIDEO_ID, "concluido": True})

    assert resposta.status_code == 200
    assert views.registros  # a view DELE foi registrada (assistir é para todos)
    assert banco["dias_estudo"] == []  # ...sem dia de estudo (privacidade)


def test_conclusao_de_video_inexistente_nao_grava_dia(ambiente):
    banco, _, _, client = ambiente
    resposta = client.post(
        "/watch",
        json={"video_id": "nao-existe-000", "concluido": True},
    )
    assert resposta.status_code == 200  # a view é registrada mesmo assim
    assert banco["dias_estudo"] == []


def test_user_id_do_corpo_e_ignorado_quando_ha_sessao(ambiente):
    """Cookie vence: dia e view caem na conta da SESSÃO, não do corpo."""
    banco, views, estudante, client = ambiente
    resposta = client.post(
        "/watch",
        json={
            "video_id": VIDEO_ID,
            "user_id": "vítima-do-furto-de-histórico",
            "concluido": True,
        },
    )

    assert resposta.status_code == 200
    assert views.registros == [(VIDEO_ID, estudante["usuario_id"])]
    assert banco["dias_estudo"][0]["usuario_id"] == estudante["usuario_id"]
    assert all(
        linha["usuario_id"] != "vítima-do-furto-de-histórico"
        for linha in banco["dias_estudo"]
    )


def test_falha_do_streak_nao_derruba_a_view(ambiente, monkeypatch):
    """Banco de dias fora do ar: a view sobrevive (streak é best-effort)."""
    _, views, _, client = ambiente

    def boom(*args, **kwargs):
        raise RuntimeError("dias_estudo indisponível")

    monkeypatch.setattr("app.streak.repo.get_client", boom)
    resposta = client.post("/watch", json={"video_id": VIDEO_ID, "concluido": True})

    assert resposta.status_code == 200
    assert views.registros


# ---------------------------------------------------------------------------
# GET /streak/meu — card do aluno
# ---------------------------------------------------------------------------

def test_streak_zero_para_quem_nunca_estudou(ambiente):
    _, _, _, client = ambiente
    resposta = client.get("/streak/meu")

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["streak"] == 0
    assert corpo["marco"] is None


def test_streak_conta_apos_concluir_um_video(ambiente):
    banco, _, _, client = ambiente
    client.post("/watch", json={"video_id": VIDEO_ID, "concluido": True})

    resposta = client.get("/streak/meu")
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["streak"] == 1
    assert corpo["estudou_hoje"] is True
    assert corpo["registrado_hoje"] is True


def test_streak_soma_dias_anteriores(ambiente):
    banco, _, estudante, client = ambiente
    hoje = calculo.dia_de_hoje()
    for offset in (1, 2):  # ontem e anteontem já estudados
        banco["dias_estudo"].append({
            "usuario_id": estudante["usuario_id"],
            "dia": (hoje - timedelta(days=offset)).isoformat(),
            "registrado_em": "2026-01-01T00:00:00+00:00",
        })
    client.post("/watch", json={"video_id": VIDEO_ID, "concluido": True})

    corpo = client.get("/streak/meu").json()
    assert corpo["streak"] == 3
    assert corpo["marco"] == 3
    assert corpo["mensagem"] == "3 dias seguidos!"


def test_streak_para_professor_responde_403(ambiente):
    """Privacidade: o professor não tem streak — nem vê o de ninguém."""
    banco, _, _, _ = ambiente
    _, client = usuario_com_sessao(
        banco, role="professor", nome="Prof.", email="prof@escola.com"
    )
    resposta = client.get("/streak/meu")
    assert resposta.status_code == 403
    assert "estudantes" in resposta.json()["detail"]


def test_streak_para_admin_responde_403(ambiente):
    banco, _, _, _ = ambiente
    _, client = usuario_com_sessao(
        banco, role="admin", nome="Admin", email="admin@edustream.com"
    )
    assert client.get("/streak/meu").status_code == 403


def test_streak_sem_sessao_responde_401(ambiente):
    assert TestClient(app).get("/streak/meu").status_code == 401


def test_streak_com_conta_bloqueada_responde_403_de_primeiro_acesso(monkeypatch):
    """Uma conta em 1º acesso não consulta o streak (guard `autenticado`)."""
    banco = banco_novo()
    trocar_get_client(monkeypatch, banco, *MODULOS)
    inserir_usuario(
        banco,
        nome="Aluno novo",
        email="novato@escola.com",
        role="estudante",
        deve_trocar_senha=True,
    )
    client = TestClient(app)
    client.post("/login", json={"email": "novato@escola.com", "senha": "senha12345"})
    resposta = client.get("/streak/meu")
    assert resposta.status_code == 403
    assert "Primeiro acesso" in resposta.json()["detail"]
