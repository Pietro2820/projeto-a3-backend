"""
Testes unitários da aplicação FastAPI (`app/main.py`).

Validam a "porta de entrada" do projeto: o health check, o registro das
rotas de cada módulo (upload e status) e a geração do OpenAPI — que é o que
garante que a aplicação "roda/funciona" no dia da apresentação.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def rotas_registradas() -> set[str]:
    """Caminhos expostos pela aplicação.

    `getattr` com default mantém o teste válido em qualquer versão do
    FastAPI/Starlette: versões novas incluem objetos auxiliarmente na lista
    `app.routes` (ex: `_IncludedRouter`) que não têm o atributo `path`.
    """
    return {caminho for rota in app.routes if (caminho := getattr(rota, "path", None))}


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

def test_health_check_retorna_200():
    resposta = client.get("/")

    assert resposta.status_code == 200


def test_health_check_informa_status_ok():
    resposta = client.get("/")

    assert resposta.json()["status"] == "ok"


def test_health_check_lista_as_rotas_disponiveis():
    resposta = client.get("/")

    assert "/upload" in resposta.json()["rotas"]


# ---------------------------------------------------------------------------
# Registro das rotas (integração entre os módulos)
# ---------------------------------------------------------------------------

def test_rota_de_upload_esta_registrada():
    assert "/upload" in rotas_registradas()


def test_rota_de_status_esta_registrada():
    assert "/status/{video_id}" in rotas_registradas()


@pytest.mark.parametrize(
    "rota",
    [
        "/",
        "/login",
        "/cadastro",
        "/upload",
        "/status/{video_id}",
        "/videos/{video_id}/relacionados",
        "/recomendacoes/{user_id}",
        "/trending",
        "/watch",
    ],
)
def test_todas_as_rotas_do_contrato_estao_na_aplicacao(rota):
    assert rota in rotas_registradas()


# ---------------------------------------------------------------------------
# Documentação automática (OpenAPI/Swagger)
# ---------------------------------------------------------------------------

def test_openapi_e_gerado_com_sucesso():
    resposta = client.get("/openapi.json")

    assert resposta.status_code == 200
    assert resposta.json()["openapi"].startswith("3.")


def test_openapi_lista_os_endpoints_do_contrato():
    caminhos = client.get("/openapi.json").json()["paths"]

    for rota in (
        "/login",
        "/cadastro",
        "/upload",
        "/status/{video_id}",
        "/videos/{video_id}/relacionados",
        "/recomendacoes/{user_id}",
        "/trending",
        "/watch",
    ):
        assert rota in caminhos, f"rota {rota} ausente do OpenAPI"


def test_titulo_da_api_descreve_a_plataforma():
    info = client.get("/openapi.json").json()["info"]

    assert info["title"] == "Plataforma de Vídeo Educacional"
    assert "educacionais" in info["description"].lower()


# ---------------------------------------------------------------------------
# Tratamento de erro em rotas inválidas
# ---------------------------------------------------------------------------

def test_rota_inexistente_retorna_404():
    resposta = client.get("/rota-que-nao-existe")

    assert resposta.status_code == 404


def test_get_no_upload_retorna_405():
    """/upload só aceita POST — GET precisa responder 405, não 500."""
    resposta = client.get("/upload")

    assert resposta.status_code == 405


def test_upload_sem_sessao_responde_401():
    """/upload agora exige professor/admin: o GUARDA DE SESSÃO vira antes da
    validação dos campos — requisição vazia sem cookie é 401 (não 422)."""
    resposta = client.post("/upload")

    assert resposta.status_code == 401
