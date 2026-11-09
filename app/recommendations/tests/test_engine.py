import pytest
from app.recommendations.engine import recomendar_videos_por_jaccard


def test_deve_recomendar_videos_ordenados_por_similaridade_jaccard():
    tags_video_assistido = ["comédia", "ação"]

    banco_de_videos = [
        {"titulo": "Vídeo 1", "tags": ["drama", "romance"]},
        {"titulo": "Vídeo 2", "tags": ["aventura", "ação"]},
        {"titulo": "Vídeo 3", "tags": ["comédia", "ação"]}
    ]

    resultado = recomendar_videos_por_jaccard(tags_video_assistido, banco_de_videos)

    assert resultado[0] == "Vídeo 3" 
    assert resultado[1] == "Vídeo 2" 
    assert "Vídeo 1" not in resultado