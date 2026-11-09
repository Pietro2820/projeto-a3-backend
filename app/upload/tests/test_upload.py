import io

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.upload import router

client = TestClient(app)


@pytest.fixture(autouse=True)
def configurar_testes(tmp_path, monkeypatch):
    monkeypatch.setattr(router, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(router, "MAX_SIZE", 1024 * 1024)
    monkeypatch.setattr(router, "save_metadata", lambda metadata: metadata)
    # Isola a fila: testes unitários não publicam tasks no broker real.
    monkeypatch.setattr(router, "_enfileirar_processamento",
                        lambda video_id, file_path: "enfileirado")


def dados_validos():
    return {
        "titulo": "Introdução à Matemática",
        "autor": "Rafael",
        "descricao": "Vídeo sobre frações",
        "tags": "educação, matemática, frações",
        "categoria": "Ensino Fundamental",
    }


def test_upload_formato_valido():
    arquivo = io.BytesIO(b"conteudo falso de video")

    response = client.post(
        "/upload",
        files={"file": ("video.mp4", arquivo, "video/mp4")},
        data=dados_validos(),
    )

    assert response.status_code == 200

    body = response.json()
    assert "video_id" in body
    assert body["status"] == "pending"
    assert body["message"] == "Upload recebido com sucesso"


def test_arquivo_e_salvo_na_pasta_correta(tmp_path):
    arquivo = io.BytesIO(b"conteudo falso de video")

    response = client.post(
        "/upload",
        files={"file": ("video.mp4", arquivo, "video/mp4")},
        data=dados_validos(),
    )

    assert response.status_code == 200

    video_id = response.json()["video_id"]
    caminho_esperado = tmp_path / video_id / "original.mp4"

    assert caminho_esperado.exists()


def test_upload_formato_invalido():
    arquivo = io.BytesIO(b"texto simples")

    response = client.post(
        "/upload",
        files={"file": ("texto.txt", arquivo, "text/plain")},
        data=dados_validos(),
    )

    assert response.status_code == 400


def test_upload_acima_do_tamanho_maximo(monkeypatch):
    monkeypatch.setattr(router, "MAX_SIZE", 10)

    arquivo = io.BytesIO(b"0123456789123456789")

    response = client.post(
        "/upload",
        files={"file": ("video.mp4", arquivo, "video/mp4")},
        data=dados_validos(),
    )

    assert response.status_code == 400


def test_upload_sem_titulo():
    dados = dados_validos()
    dados.pop("titulo")

    arquivo = io.BytesIO(b"conteudo falso de video")

    response = client.post(
        "/upload",
        files={"file": ("video.mp4", arquivo, "video/mp4")},
        data=dados,
    )

    assert response.status_code == 422


def test_upload_sem_autor():
    dados = dados_validos()
    dados.pop("autor")

    arquivo = io.BytesIO(b"conteudo falso de video")

    response = client.post(
        "/upload",
        files={"file": ("video.mp4", arquivo, "video/mp4")},
        data=dados,
    )

    assert response.status_code == 422


def test_upload_com_titulo_vazio():
    dados = dados_validos()
    dados["titulo"] = "   "

    arquivo = io.BytesIO(b"conteudo falso de video")

    response = client.post(
        "/upload",
        files={"file": ("video.mp4", arquivo, "video/mp4")},
        data=dados,
    )

    assert response.status_code == 400


def test_upload_com_autor_vazio():
    dados = dados_validos()
    dados["autor"] = "   "

    arquivo = io.BytesIO(b"conteudo falso de video")

    response = client.post(
        "/upload",
        files={"file": ("video.mp4", arquivo, "video/mp4")},
        data=dados,
    )

    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Integração upload → fila → status (a "conversa" com o frontend)
# ---------------------------------------------------------------------------

def test_resposta_inclui_fila_e_status_url():
    """O front usa status_url para acompanhar o processamento via polling."""
    arquivo = io.BytesIO(b"conteudo falso de video")

    response = client.post(
        "/upload",
        files={"file": ("video.mp4", arquivo, "video/mp4")},
        data=dados_validos(),
    )

    body = response.json()
    assert body["fila"] == "enfileirado"
    assert body["status_url"] == f"/status/{body['video_id']}"


def test_processamento_e_enfileirado_com_video_id_e_caminho(tmp_path, monkeypatch):
    chamadas = []
    monkeypatch.setattr(
        router, "_enfileirar_processamento",
        lambda video_id, file_path: chamadas.append((video_id, file_path)) or "enfileirado",
    )

    arquivo = io.BytesIO(b"conteudo falso de video")
    response = client.post(
        "/upload",
        files={"file": ("video.mp4", arquivo, "video/mp4")},
        data=dados_validos(),
    )

    video_id = response.json()["video_id"]
    assert chamadas == [(video_id, str(tmp_path / video_id / "original.mp4"))]


def test_broker_fora_do_ar_nao_derruba_o_upload(monkeypatch):
    """Fila indisponível: o upload continua válido, só avisa em 'fila'."""
    def _fila_indisponivel(video_id, file_path):
        return "indisponivel"

    monkeypatch.setattr(router, "_enfileirar_processamento", _fila_indisponivel)

    arquivo = io.BytesIO(b"conteudo falso de video")
    response = client.post(
        "/upload",
        files={"file": ("video.mp4", arquivo, "video/mp4")},
        data=dados_validos(),
    )

    assert response.status_code == 200
    assert response.json()["fila"] == "indisponivel"


def test_banco_indisponivel_devolve_503_com_orientacao(monkeypatch):
    """Tabela 'videos' não criada (ou credencial errada) não pode virar 500."""
    def _boom(metadata):
        raise Exception("relation public.videos does not exist")

    monkeypatch.setattr(router, "save_metadata", _boom)

    arquivo = io.BytesIO(b"conteudo falso de video")
    response = client.post(
        "/upload",
        files={"file": ("video.mp4", arquivo, "video/mp4")},
        data=dados_validos(),
    )

    assert response.status_code == 503
    assert "schema.sql" in response.json()["detail"]
