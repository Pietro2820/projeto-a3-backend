"""
Testes do banco local em JSON (fallback de dev sem Supabase).

Cobrem o módulo `local_store` isolado (com LOCAL_DB_PATH apontando para
tmp_path) e o fallback automático de `app/database/videos.py`: sem
credenciais do Supabase, as funções do contrato #2 precisam funcionar
exatamente igual — é o que permite a integração back ↔ front rodar sem
serviço externo.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.database import local_store, videos


@pytest.fixture(autouse=True)
def banco_temporario(tmp_path, monkeypatch):
    """Todas as operações deste arquivo usam um JSON dentro de tmp_path."""
    monkeypatch.setenv("LOCAL_DB_PATH", str(tmp_path / "videos.json"))
    return tmp_path / "videos.json"


METADATA_EXEMPLO = {
    "video_id": "v-123",
    "titulo": "Frações",
    "descricao": "aula demo",
    "tags": ["matemática", "frações"],
    "categoria": "Matemática",
    "autor": "Rafael",
    "status": "pending",
}


# ---------------------------------------------------------------------------
# local_store — operações básicas
# ---------------------------------------------------------------------------

def test_inserir_devolve_o_registro_e_cria_o_arquivo(banco_temporario):
    registro = local_store.inserir(METADATA_EXEMPLO)

    assert registro["video_id"] == "v-123"
    assert banco_temporario.is_file()


def test_inserir_preenche_video_id_e_status_quando_faltam():
    registro = local_store.inserir({"titulo": "Sem id"})

    assert registro["video_id"]
    assert registro["status"] == "pending"
    assert registro["criado_em"]


def test_buscar_encontrado_e_nao_encontrado():
    local_store.inserir(METADATA_EXEMPLO)

    assert local_store.buscar("v-123")["titulo"] == "Frações"
    assert local_store.buscar("nao-existe") is None


def test_atualizar_status_muda_apenas_o_status():
    local_store.inserir(METADATA_EXEMPLO)

    atualizado = local_store.atualizar_status("v-123", "completed")

    assert atualizado["status"] == "completed"
    assert atualizado["titulo"] == "Frações"
    assert local_store.buscar("v-123")["status"] == "completed"


def test_atualizar_status_de_video_inexistente_devolve_none_sem_escrever(banco_temporario):
    assert local_store.atualizar_status("fantasma", "completed") is None
    assert not banco_temporario.exists()


def test_listar_com_e_sem_filtro_de_status():
    local_store.inserir({**METADATA_EXEMPLO, "video_id": "a", "status": "completed"})
    local_store.inserir({**METADATA_EXEMPLO, "video_id": "b", "status": "pending"})

    assert len(local_store.listar()) == 2
    assert [v["video_id"] for v in local_store.listar(status="completed")] == ["a"]


def test_arquivo_corrompido_vira_catalogo_vazio(banco_temporario):
    banco_temporario.write_text("{isso não é json", encoding="utf-8")

    assert local_store.listar() == []
    assert local_store.buscar("v-123") is None


# ---------------------------------------------------------------------------
# videos.py — fallback automático quando o Supabase não está configurado
# ---------------------------------------------------------------------------

@pytest.fixture
def sem_supabase():
    """Simula o cenário real de dev: get_client() levanta RuntimeError."""
    with patch(
        "app.database.videos.get_client",
        side_effect=RuntimeError("SUPABASE_URL e/ou SUPABASE_KEY não estão definidas"),
    ):
        yield


def test_inserir_video_sem_supabase_usa_o_banco_local(sem_supabase):
    registro = videos.inserir_video(METADATA_EXEMPLO)

    assert registro["video_id"] == "v-123"
    assert local_store.buscar("v-123") is not None


def test_buscar_video_sem_supabase_usa_o_banco_local(sem_supabase):
    local_store.inserir(METADATA_EXEMPLO)

    assert videos.buscar_video("v-123")["titulo"] == "Frações"
    assert videos.buscar_video("nao-existe") is None


def test_atualizar_status_sem_supabase_usa_o_banco_local(sem_supabase):
    local_store.inserir(METADATA_EXEMPLO)

    videos.atualizar_status("v-123", "completed")

    assert local_store.buscar("v-123")["status"] == "completed"


def test_listar_videos_sem_supabase_usa_o_banco_local(sem_supabase):
    local_store.inserir({**METADATA_EXEMPLO, "video_id": "a", "status": "completed"})
    local_store.inserir({**METADATA_EXEMPLO, "video_id": "b", "status": "pending"})

    assert len(videos.listar_videos(status="completed")) == 1


def test_criar_video_sem_supabase_usa_o_banco_local(sem_supabase):
    registro = videos.criar_video(titulo="Nova aula", categoria="Física")

    assert registro["titulo"] == "Nova aula"
    assert local_store.buscar(registro["video_id"]) is not None
