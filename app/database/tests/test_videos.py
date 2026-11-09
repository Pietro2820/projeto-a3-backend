from unittest.mock import MagicMock, patch
from app.database import videos


def _mock_response(data):
    mock = MagicMock()
    mock.data = data
    return mock


@patch("app.database.videos.get_client")
def test_criar_video(mock_get_client):
    mock_client = MagicMock()
    mock_client.table.return_value.insert.return_value.execute.return_value = (
        _mock_response([{"video_id": "abc-123", "titulo": "Aula 1", "status": "pending"}])
    )
    mock_get_client.return_value = mock_client

    resultado = videos.criar_video(titulo="Aula 1", categoria="Matemática")

    assert resultado["video_id"] == "abc-123"
    assert resultado["status"] == "pending"
    mock_client.table.assert_called_with("videos")


@patch("app.database.videos.get_client")
def test_buscar_video_encontrado(mock_get_client):
    mock_client = MagicMock()
    mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = (
        _mock_response([{"video_id": "abc-123", "titulo": "Aula 1"}])
    )
    mock_get_client.return_value = mock_client

    resultado = videos.buscar_video("abc-123")

    assert resultado["video_id"] == "abc-123"


@patch("app.database.videos.get_client")
def test_buscar_video_nao_encontrado(mock_get_client):
    mock_client = MagicMock()
    mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = (
        _mock_response([])
    )
    mock_get_client.return_value = mock_client

    resultado = videos.buscar_video("nao-existe")

    assert resultado is None


@patch("app.database.videos.get_client")
def test_atualizar_status(mock_get_client):
    mock_client = MagicMock()
    mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = (
        _mock_response([{"video_id": "abc-123", "status": "completed"}])
    )
    mock_get_client.return_value = mock_client

    resultado = videos.atualizar_status("abc-123", "completed")

    assert resultado["status"] == "completed"


@patch("app.database.videos.get_client")
def test_listar_videos_por_status(mock_get_client):
    mock_client = MagicMock()
    mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = (
        _mock_response([{"video_id": "abc-123", "status": "pending"}])
    )
    mock_get_client.return_value = mock_client

    resultado = videos.listar_videos(status="pending")

    assert len(resultado) == 1
    assert resultado[0]["status"] == "pending"