"""
Bateria E2E da propriedade de vídeo — app/videos/router.py (migração 002).

Cobre o contrato consumido pelo frontend (js/meus-videos.js e
js/painel-admin.js):

  * GET    /meus-videos — 401 sem sessão, 403 estudante, filtro NO BANCO
    (só os vídeos do professor da sessão) e views no item;
  * PATCH  /videos/{video_id} — dono edita (merge, tags string→lista,
    título aparado), não-dono leva 403, admin modera, 404 inexistente,
    422 título vazio/corpo vazio e o status do pipeline é IMUNE ao PATCH;
  * DELETE /videos/{video_id} — dono apaga (registro some do banco e as
    pastas uploads/ e videos/ são limpas), não-dono não apaga, 404.

Sem rede: o Supabase é dublê pelo ClienteFalso de app/tests/fakes.py — o
mesmo padrão dos testes de papéis, admin e streak.
"""
from __future__ import annotations

import uuid

import pytest

from app.tests.fakes import (
    banco_novo,
    trocar_get_client,
    usuario_com_sessao,
)

# Módulos que importam get_client para o PRÓPRIO namespace (login usa a
# tabela usuarios; o router de vídeos usa a tabela videos).
MODULOS_COM_BANCO = ("app.database.videos", "app.auth.usuarios")


@pytest.fixture
def banco(monkeypatch):
    """Banco falso novo por teste, apontado nos dois namespaces."""
    banco = banco_novo()
    trocar_get_client(monkeypatch, banco, *MODULOS_COM_BANCO)
    return banco


def inserir_video(
    banco: dict,
    *,
    video_id: str,
    professor_id: str,
    titulo: str = "Aula de teste",
    status: str = "completed",
    tags: list | None = None,
) -> dict:
    """Linha da tabela videos direto no banco falso (como o upload deixaria)."""
    registro = {
        "video_id": video_id,
        "titulo": titulo,
        "descricao": "descrição original",
        "tags": tags if tags is not None else ["teste"],
        "categoria": "geral",
        "autor": "Professor",
        "professor_id": professor_id,
        "status": status,
        "criado_em": "2026-01-01T00:00:00+00:00",
    }
    banco["videos"].append(registro)
    return registro


def video_do_banco(banco: dict, video_id: str) -> dict | None:
    return next(
        (v for v in banco["videos"] if v["video_id"] == video_id), None
    )


# ---------------------------------------------------------------------------
# GET /meus-videos
# ---------------------------------------------------------------------------

def test_meus_videos_exige_sessao(banco):
    from fastapi.testclient import TestClient
    from app.main import app

    resposta = TestClient(app).get("/meus-videos")
    assert resposta.status_code == 401


def test_meus_videos_bloqueia_estudante(banco):
    _, client = usuario_com_sessao(
        banco, role="estudante", nome="Ana", email="ana@escola.edu"
    )
    resposta = client.get("/meus-videos")
    assert resposta.status_code == 403


def test_meus_videos_soh_do_professor_da_sessao(banco):
    dono, client = usuario_com_sessao(
        banco, role="professor", nome="Marcos", email="marcos@escola.edu"
    )
    outro, _ = usuario_com_sessao(
        banco, role="professor", nome="Outro", email="outro@escola.edu"
    )

    inserir_video(banco, video_id="v-meu-1", professor_id=dono["usuario_id"])
    inserir_video(banco, video_id="v-meu-2", professor_id=dono["usuario_id"],
                  status="pending")
    inserir_video(banco, video_id="v-alheio", professor_id=outro["usuario_id"])

    resposta = client.get("/meus-videos")
    assert resposta.status_code == 200

    corpo = resposta.json()
    ids = {item["video_id"] for item in corpo["videos"]}
    assert ids == {"v-meu-1", "v-meu-2"}          # filtro NO BANCO
    assert corpo["total"] == 2
    # qualquer status aparece (o painel mostra o que está na fila também)
    status_por_id = {i["video_id"]: i["status"] for i in corpo["videos"]}
    assert status_por_id["v-meu-2"] == "pending"
    # views presentes (Redis indisponível no teste → 0, sem quebrar)
    assert all("views" in item for item in corpo["videos"])


# ---------------------------------------------------------------------------
# PATCH /videos/{video_id}
# ---------------------------------------------------------------------------

def test_patch_pelo_dono_atualiza_metadados(banco):
    dono, client = usuario_com_sessao(
        banco, role="professor", nome="Marcos", email="marcos@escola.edu"
    )
    inserir_video(banco, video_id="v-1", professor_id=dono["usuario_id"])

    resposta = client.patch(
        "/videos/v-1",
        json={"titulo": "  Novo título  ", "tags": "hls, ffmpeg",
              "descricao": "Nova descrição", "categoria": "redes"},
    )
    assert resposta.status_code == 200

    corpo = resposta.json()
    assert corpo["video"]["titulo"] == "Novo título"     # aparado
    assert corpo["video"]["tags"] == ["hls", "ffmpeg"]   # string → lista
    assert corpo["video"]["descricao"] == "Nova descrição"
    assert corpo["video"]["categoria"] == "redes"

    linha = video_do_banco(banco, "v-1")
    assert linha["titulo"] == "Novo título"              # foi NO BANCO
    assert linha["tags"] == ["hls", "ffmpeg"]


def test_patch_aceita_tags_como_lista(banco):
    dono, client = usuario_com_sessao(
        banco, role="professor", nome="Marcos", email="marcos@escola.edu"
    )
    inserir_video(banco, video_id="v-1", professor_id=dono["usuario_id"])

    resposta = client.patch("/videos/v-1", json={"tags": ["a", " b ", ""]})
    assert resposta.status_code == 200
    assert resposta.json()["video"]["tags"] == ["a", "b"]


def test_patch_por_nao_dono_recebe_403(banco):
    dono, _ = usuario_com_sessao(
        banco, role="professor", nome="Marcos", email="marcos@escola.edu"
    )
    intruso, client = usuario_com_sessao(
        banco, role="professor", nome="Intruso", email="intruso@escola.edu"
    )
    inserir_video(banco, video_id="v-1", professor_id=dono["usuario_id"])

    resposta = client.patch("/videos/v-1", json={"titulo": "Hacked!"})
    assert resposta.status_code == 403
    assert "pertence a outro professor" in resposta.json()["detail"]
    assert video_do_banco(banco, "v-1")["titulo"] == "Aula de teste"  # intacto


def test_patch_estudante_tambem_e_bloqueado(banco):
    dono, _ = usuario_com_sessao(
        banco, role="professor", nome="Marcos", email="marcos@escola.edu"
    )
    aluno, client = usuario_com_sessao(
        banco, role="estudante", nome="Ana", email="ana@escola.edu"
    )
    inserir_video(banco, video_id="v-1", professor_id=dono["usuario_id"])

    assert client.patch("/videos/v-1", json={"titulo": "x"}).status_code == 403


def test_patch_admin_modera_video_alheio(banco):
    dono, _ = usuario_com_sessao(
        banco, role="professor", nome="Marcos", email="marcos@escola.edu"
    )
    _, client = usuario_com_sessao(
        banco, role="admin", nome="Root", email="root@escola.edu"
    )
    inserir_video(banco, video_id="v-1", professor_id=dono["usuario_id"])

    resposta = client.patch("/videos/v-1", json={"titulo": "Renomeado"})
    assert resposta.status_code == 200
    assert video_do_banco(banco, "v-1")["titulo"] == "Renomeado"


def test_patch_nao_deixa_mudar_status_do_pipeline(banco):
    dono, client = usuario_com_sessao(
        banco, role="professor", nome="Marcos", email="marcos@escola.edu"
    )
    inserir_video(banco, video_id="v-1", professor_id=dono["usuario_id"],
                  status="pending")

    # cliente mal-intencionado tenta pular a fila: o campo extra é ignorado
    resposta = client.patch(
        "/videos/v-1",
        json={"titulo": "Título novo", "status": "completed"},
    )
    assert resposta.status_code == 200
    assert video_do_banco(banco, "v-1")["status"] == "pending"


def test_patch_titulo_vazio_recebe_422(banco):
    dono, client = usuario_com_sessao(
        banco, role="professor", nome="Marcos", email="marcos@escola.edu"
    )
    inserir_video(banco, video_id="v-1", professor_id=dono["usuario_id"])

    resposta = client.patch("/videos/v-1", json={"titulo": "   "})
    assert resposta.status_code == 422
    assert video_do_banco(banco, "v-1")["titulo"] == "Aula de teste"


def test_patch_sem_campos_recebe_422(banco):
    dono, client = usuario_com_sessao(
        banco, role="professor", nome="Marcos", email="marcos@escola.edu"
    )
    inserir_video(banco, video_id="v-1", professor_id=dono["usuario_id"])

    resposta = client.patch("/videos/v-1", json={})
    assert resposta.status_code == 422


def test_patch_video_inexistente_recebe_404(banco):
    _, client = usuario_com_sessao(
        banco, role="professor", nome="Marcos", email="marcos@escola.edu"
    )
    resposta = client.patch("/videos/nao-existe", json={"titulo": "x"})
    assert resposta.status_code == 404


def test_patch_sem_sessao_recebe_401(banco):
    from fastapi.testclient import TestClient
    from app.main import app

    resposta = TestClient(app).patch("/videos/v-1", json={"titulo": "x"})
    assert resposta.status_code == 401


# ---------------------------------------------------------------------------
# DELETE /videos/{video_id}
# ---------------------------------------------------------------------------

def test_delete_pelo_dono_remove_do_banco_e_do_disco(banco, tmp_path, monkeypatch):
    import app.videos.router as router_videos

    dono, client = usuario_com_sessao(
        banco, role="professor", nome="Marcos", email="marcos@escola.edu"
    )
    video_id = str(uuid.uuid4())
    inserir_video(banco, video_id=video_id, professor_id=dono["usuario_id"])

    # pastas de arquivo reais (temporárias) para provar o cleanup
    pasta_uploads = tmp_path / "uploads" / video_id
    pasta_midia = tmp_path / "videos" / video_id
    pasta_uploads.mkdir(parents=True)
    pasta_midia.mkdir(parents=True)
    (pasta_uploads / "original.mp4").write_bytes(b"mp4")
    (pasta_midia / "master.m3u8").write_text("#EXTM3U")
    monkeypatch.setattr(router_videos, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(router_videos, "VIDEOS_DIR", tmp_path / "videos")

    resposta = client.delete(f"/videos/{video_id}")
    assert resposta.status_code == 200
    assert resposta.json()["video_id"] == video_id
    assert resposta.json()["arquivos_removidos"] == {"uploads": True, "midia": True}

    assert video_do_banco(banco, video_id) is None       # saiu do banco
    assert not pasta_uploads.exists()                    # original.mp4 foi embora
    assert not pasta_midia.exists()                      # HLS foi embora


def test_delete_por_nao_dono_recebe_403(banco):
    dono, _ = usuario_com_sessao(
        banco, role="professor", nome="Marcos", email="marcos@escola.edu"
    )
    _, client = usuario_com_sessao(
        banco, role="professor", nome="Intruso", email="intruso@escola.edu"
    )
    inserir_video(banco, video_id="v-1", professor_id=dono["usuario_id"])

    resposta = client.delete("/videos/v-1")
    assert resposta.status_code == 403
    assert video_do_banco(banco, "v-1") is not None      # não apagou


def test_delete_admin_modera_video_alheio(banco):
    dono, _ = usuario_com_sessao(
        banco, role="professor", nome="Marcos", email="marcos@escola.edu"
    )
    _, client = usuario_com_sessao(
        banco, role="admin", nome="Root", email="root@escola.edu"
    )
    inserir_video(banco, video_id="v-1", professor_id=dono["usuario_id"])

    resposta = client.delete("/videos/v-1")
    assert resposta.status_code == 200
    assert video_do_banco(banco, "v-1") is None


def test_delete_inexistente_recebe_404(banco):
    _, client = usuario_com_sessao(
        banco, role="professor", nome="Marcos", email="marcos@escola.edu"
    )
    assert client.delete("/videos/nao-existe").status_code == 404


def test_delete_sem_sessao_recebe_401(banco):
    from fastapi.testclient import TestClient
    from app.main import app

    assert TestClient(app).delete("/videos/v-1").status_code == 401


# ---------------------------------------------------------------------------
# Integração com o resto da API
# ---------------------------------------------------------------------------

def test_rotas_do_router_estao_no_contrato_da_health(banco):
    from fastapi.testclient import TestClient
    from app.main import app

    # Mounts (/videos, /player) não têm methods — só rotas de API interessam
    rotas = {
        rota.path + "|" + ",".join(sorted(getattr(rota, "methods", [])))
        for rota in app.routes
        if hasattr(rota, "methods")
    }

    assert "/meus-videos|GET" in rotas
    assert "/videos/{video_id}|PATCH" in rotas
    assert "/videos/{video_id}|DELETE" in rotas
    # e a OpenAPI publica o contrato que o frontend consome
    caminhos = TestClient(app).get("/openapi.json").json()["paths"]
    assert "/meus-videos" in caminhos
    assert "/videos/{video_id}" in caminhos
