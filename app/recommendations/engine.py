"""
Motor de recomendações — feature/recomendacoes (Gustavo).

A ideia é simples e adequada ao porte do projeto: dois vídeos são parecidos se
compartilham tags. A similaridade de Jaccard mede isso como

    |A ∩ B| / |A ∪ B|     (0 = nada em comum, 1 = mesmas tags)

Não precisa de treino, não precisa de biblioteca pesada e o resultado é fácil
de explicar na apresentação — que é o que importa para uma plataforma
educacional onde o catálogo ainda é pequeno.

Este módulo é pura lógica: não fala com banco, nem com Redis, nem com HTTP.
Quem faz isso é o `router.py`. É de propósito — assim dá para testar tudo sem
infraestrutura.
"""
from __future__ import annotations


def normalizar_tags(tags) -> list[str]:
    """Devolve uma lista limpa de tags a partir de qualquer formato conhecido.

    O projeto guarda tags de dois jeitos (e os dois chegam aqui):
      * lista  — vem do `parse_tags()` do upload (ex: ["educação", "math"])
      * string — vem de quem insere direto, separada por vírgula
      * None   — vídeo cadastrado sem tags

    Sem isso, `set(None)` estouraria TypeError no meio da recomendação.
    """
    if tags is None:
        return []

    if isinstance(tags, str):
        brutas = tags.split(",")
    else:
        try:
            brutas = list(tags)
        except TypeError:
            return []

    return [str(tag).strip().lower() for tag in brutas if str(tag).strip()]


def calcular_jaccard(tags_video_1, tags_video_2) -> float:
    """Similaridade de Jaccard entre dois conjuntos de tags."""
    set1 = set(normalizar_tags(tags_video_1))
    set2 = set(normalizar_tags(tags_video_2))

    if not set1 and not set2:
        return 0.0

    intersecao = len(set1.intersection(set2))
    uniao = len(set1.union(set2))

    return float(intersecao) / uniao


def ordenar_por_similaridade(
    tags_video_atual,
    lista_todos_videos: list[dict],
    limite_minimo: float = 0.1,
) -> list[dict]:
    """Ordena os vídeos do mais parecido para o menos parecido.

    Diferente de `recomendar_videos_por_jaccard`, devolve os dicionários
    completos com o campo `score` acrescentado — é o que a API precisa para
    montar a resposta (título, thumbnail, duração, etc.), não só o título.

    Args:
        tags_video_atual: tags do vídeo que o usuário está assistindo.
        lista_todos_videos: vídeos candidatos; cada um precisa ter "tags".
        limite_minimo: nota de corte. Abaixo disso o vídeo nem entra na lista
            (evita recomendar coisa que só compartilha uma tag genérica).

    Returns:
        Lista de dicts originais + {"score": float}, em ordem decrescente.
    """
    candidatos = []

    for video in lista_todos_videos:
        nota = calcular_jaccard(tags_video_atual, video.get("tags"))
        if nota >= limite_minimo:
            candidatos.append({**video, "score": round(nota, 2)})

    # `sort` é estável: em caso de empate mantém a ordem em que vieram do banco.
    candidatos.sort(key=lambda item: item["score"], reverse=True)
    return candidatos


def recomendar_videos_por_jaccard(
    video_atual_tags,
    lista_todos_videos: list[dict],
    limite_minimo: float = 0.1,
) -> list[str]:
    """Devolve só os títulos recomendados, do mais parecido para o menos.

    Mantida com a assinatura original (é a que o teste do Gustavo usa).
    """
    ordenados = ordenar_por_similaridade(
        video_atual_tags, lista_todos_videos, limite_minimo
    )
    return [video["titulo"] for video in ordenados]
