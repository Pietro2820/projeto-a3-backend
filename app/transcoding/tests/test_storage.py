"""
Testes da integração com S3/MinIO (Passo 6 — critério "serviço externo" do edital).

Usam `moto` para simular a AWS em memória: os testes rodam sem conta na AWS,
sem MinIO de pé e sem custo. A troca de MinIO <-> AWS S3 é só configuração
(endpoint_url), então o comportamento coberto aqui vale para os dois.

Se boto3/moto não estiverem instalados, o módulo inteiro é pulado com aviso.
"""
from pathlib import Path

import pytest

boto3 = pytest.importorskip("boto3", reason="boto3 não instalado (pip install boto3)")
moto = pytest.importorskip("moto", reason="moto não instalado (pip install moto[s3])")
from moto import mock_aws  # noqa: E402

from app.transcoding.errors import StorageError  # noqa: E402
from app.transcoding.storage import VideoStorage, content_type_de  # noqa: E402

BUCKET_TESTE = "videos-plataforma-teste"
VIDEO_ID = "550e8400-e29b-41d4-a716-446655440000"


@pytest.fixture
def storage():
    """VideoStorage apontando para um S3 fake (moto) com bucket já criado."""
    with mock_aws():
        cliente = boto3.client("s3", region_name="us-east-1")
        cliente.create_bucket(Bucket=BUCKET_TESTE)
        yield VideoStorage(bucket=BUCKET_TESTE, client=cliente, region="us-east-1")


@pytest.fixture
def arvore_processados(tmp_path) -> Path:
    """Simula a saída da transcodificação: videos/{id}/ com HLS + thumbnail."""
    raiz = tmp_path / "videos" / VIDEO_ID
    (raiz / "360p").mkdir(parents=True)
    (raiz / "360p" / "playlist.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
    (raiz / "360p" / "segment_000.ts").write_bytes(b"\x47" * 188)
    (raiz / "master.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
    (raiz / "thumbnail.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"0" * 64)
    return raiz


def _listar_chaves(cliente) -> list[str]:
    resposta = cliente.list_objects_v2(Bucket=BUCKET_TESTE)
    return [obj["Key"] for obj in resposta.get("Contents", [])]


# ---------------------------------------------------------------------------
# Upload de arquivo simples
# ---------------------------------------------------------------------------
def test_upload_arquivo_envia_conteudo_integro(storage, tmp_path):
    arquivo = tmp_path / "dados.txt"
    arquivo.write_text("conteúdo do arquivo", encoding="utf-8")

    chave = storage.upload_arquivo(arquivo, "teste/dados.txt")

    assert chave == "teste/dados.txt"
    baixado = storage._client.get_object(Bucket=BUCKET_TESTE, Key=chave)
    assert baixado["Body"].read().decode("utf-8") == "conteúdo do arquivo"


def test_upload_arquivo_inexistente_levanta_storage_error(storage, tmp_path):
    with pytest.raises(StorageError):
        storage.upload_arquivo(tmp_path / "fantasma.m3u8", "x/fantasma.m3u8")


# ---------------------------------------------------------------------------
# Content-Type correto (players/CDN dependem disso)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "extensao,esperado",
    [
        (".m3u8", "application/vnd.apple.mpegurl"),
        (".ts", "video/mp2t"),
        (".mp4", "video/mp4"),
        (".jpg", "image/jpeg"),
        (".desconhecido", "application/octet-stream"),
    ],
)
def test_content_type_por_extensao(extensao, esperado):
    assert content_type_de(f"arquivo{extensao}") == esperado


def test_upload_define_content_type_no_objeto(storage, tmp_path):
    playlist = tmp_path / "playlist.m3u8"
    playlist.write_text("#EXTM3U\n", encoding="utf-8")

    storage.upload_arquivo(playlist, "videos/x/playlist.m3u8")

    cabecalho = storage._client.head_object(Bucket=BUCKET_TESTE, Key="videos/x/playlist.m3u8")
    assert cabecalho["ContentType"] == "application/vnd.apple.mpegurl"


# ---------------------------------------------------------------------------
# Chaves no formato do contrato #1 da documentação técnica
# ---------------------------------------------------------------------------
def test_upload_original_segundo_contrato(storage, tmp_path):
    original = tmp_path / "original.mp4"
    original.write_bytes(b"\x00" * 128)

    chave = storage.upload_original(VIDEO_ID, original)

    assert chave == f"uploads/{VIDEO_ID}/original.mp4"
    assert storage.existe(chave)


def test_upload_processados_preserva_estrutura(storage, arvore_processados):
    chaves = storage.upload_processados(VIDEO_ID, arvore_processados)

    assert set(chaves) == {
        f"videos/{VIDEO_ID}/360p/playlist.m3u8",
        f"videos/{VIDEO_ID}/360p/segment_000.ts",
        f"videos/{VIDEO_ID}/master.m3u8",
        f"videos/{VIDEO_ID}/thumbnail.jpg",
    }
    assert set(_listar_chaves(storage._client)) == set(chaves)


def test_upload_processados_diretorio_inexistente_levanta_erro(storage, tmp_path):
    with pytest.raises(StorageError):
        storage.upload_processados(VIDEO_ID, tmp_path / "nao_existe")


def test_upload_processados_diretorio_vazio_levanta_erro(storage, tmp_path):
    vazio = tmp_path / "vazio"
    vazio.mkdir()
    with pytest.raises(StorageError):
        storage.upload_processados(VIDEO_ID, vazio)


# ---------------------------------------------------------------------------
# URLs e configuração via ambiente
# ---------------------------------------------------------------------------
def test_url_publica_formato_minio():
    storage = VideoStorage(bucket=BUCKET_TESTE, client=object(), endpoint_url="http://localhost:9000")
    url = storage.url_publica(f"videos/{VIDEO_ID}/master.m3u8")
    assert url == f"http://localhost:9000/{BUCKET_TESTE}/videos/{VIDEO_ID}/master.m3u8"


def test_url_publica_formato_aws():
    storage = VideoStorage(bucket=BUCKET_TESTE, client=object(), region="us-east-1")
    storage.endpoint_url = None
    url = storage.url_publica("videos/x/master.m3u8")
    assert url == f"https://{BUCKET_TESTE}.s3.us-east-1.amazonaws.com/videos/x/master.m3u8"


def test_url_assinada_contem_a_chave(storage):
    url = storage.url_assinada("videos/x/master.m3u8", expira_em_segundos=60)
    assert "videos/x/master.m3u8" in url


def test_from_env_monta_storage_configurado(monkeypatch):
    monkeypatch.setenv("S3_BUCKET", "balde-do-time")
    monkeypatch.setenv("AWS_ENDPOINT_URL", "http://minio:9000")
    with mock_aws():
        storage = VideoStorage.from_env()
    assert storage.bucket == "balde-do-time"
    assert storage.endpoint_url == "http://minio:9000"


def test_from_env_sem_bucket_levanta_erro(monkeypatch):
    monkeypatch.delenv("S3_BUCKET", raising=False)
    with pytest.raises(StorageError):
        VideoStorage.from_env()


def test_existe_retorna_false_para_chave_desconhecida(storage):
    assert storage.existe("videos/nao-existe/master.m3u8") is False
