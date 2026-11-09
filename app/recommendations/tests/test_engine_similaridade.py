"""
Testes do motor de recomendação — casos de borda que o teste original não pega.

O `test_engine.py` do Gustavo valida o caso feliz (ordenação por similaridade).
Este arquivo cobre o que quebra em produção: tags vindas como string, tags
vazias, diferença de caixa/acentos, vídeos sem a chave "tags" e o empate na
ordenação.

Não substitui o teste original — soma com ele.
"""
from __future__ import annotations

import pytest

from app.recommendations.engine import (
    calcular_jaccard,
    normalizar_tags,
    ordenar_por_similaridade,
    recomendar_videos_por_jaccard,
)


# ---------------------------------------------------------------------------
# normalizar_tags
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "entrada,esperado",
    [
        (None, []),
        ([], []),
        ("", []),
        (["matemática"], ["matemática"]),
        ("matemática", ["matemática"]),
        ("matemática, frações", ["matemática", "frações"]),
        ("  matemática ,  frações  ", ["matemática", "frações"]),
        ("matemática,,frações", ["matemática", "frações"]),
        (["Matemática", "FRAÇÕES"], ["matemática", "frações"]),
        (("a", "b"), ["a", "b"]),
    ],
)
def test_normalizar_tags(entrada, esperado):
    assert normalizar_tags(entrada) == esperado


def test_normalizar_tags_nao_quebra_com_tipo_inesperado():
    """Um inteiro no lugar das tags não pode derrubar a recomendação inteira."""
    assert normalizar_tags(42) == []


def test_normalizar_tags_sempre_devolve_lista_de_strings():
    resultado = normalizar_tags([1, 2])

    assert resultado == ["1", "2"]
    assert all(isinstance(tag, str) for tag in resultado)


# ---------------------------------------------------------------------------
# calcular_jaccard
# ---------------------------------------------------------------------------

def test_tags_identicas_tem_similaridade_maxima():
    assert calcular_jaccard(["a", "b"], ["a", "b"]) == 1.0


def test_tags_totalmente_diferentes_tem_similaridade_zero():
    assert calcular_jaccard(["a", "b"], ["c", "d"]) == 0.0


def test_sobreposicao_parcial_calcula_a_fracao_correta():
    # interseção {b} = 1, união {a,b,c} = 3
    assert calcular_jaccard(["a", "b"], ["b", "c"]) == pytest.approx(1 / 3)


def test_ambos_sem_tags_devolve_zero_em_vez_de_divisao_por_zero():
    assert calcular_jaccard([], []) == 0.0
    assert calcular_jaccard(None, None) == 0.0


def test_um_lado_sem_tags_devolve_zero():
    assert calcular_jaccard(["a"], []) == 0.0
    assert calcular_jaccard([], ["a"]) == 0.0


def test_ordem_dos_argumentos_nao_altera_o_resultado():
    assert calcular_jaccard(["a", "b"], ["b", "c"]) == calcular_jaccard(
        ["b", "c"], ["a", "b"]
    )


def test_tags_repetidas_nao_contam_duas_vezes():
    assert calcular_jaccard(["a", "a", "a"], ["a"]) == 1.0


def test_diferenca_de_caixa_nao_impede_o_casamento():
    assert calcular_jaccard(["Matemática"], ["matemática"]) == 1.0


def test_resultado_e_sempre_float_entre_zero_e_um():
    resultado = calcular_jaccard(["a", "b"], ["b", "c"])

    assert isinstance(resultado, float)
    assert 0.0 <= resultado <= 1.0


def test_tags_vindas_como_string_funcionam():
    assert calcular_jaccard("matemática, frações", "matemática, geometria") == pytest.approx(1 / 3)


# ---------------------------------------------------------------------------
# ordenar_por_similaridade
# ---------------------------------------------------------------------------

VIDEOS = [
    {"titulo": "Sem nada em comum", "tags": ["cinema"]},
    {"titulo": "Meio parecido", "tags": ["matemática", "cinema"]},
    {"titulo": "Bem parecido", "tags": ["matemática", "frações"]},
]


def test_ordena_do_mais_parecido_para_o_menos_parecido():
    resultado = ordenar_por_similaridade(["matemática", "frações"], VIDEOS, 0.0)

    assert [item["titulo"] for item in resultado] == [
        "Bem parecido",
        "Meio parecido",
        "Sem nada em comum",
    ]


def test_acrescenta_o_score_em_cada_item():
    resultado = ordenar_por_similaridade(["matemática", "frações"], VIDEOS, 0.0)

    assert resultado[0]["score"] == 1.0
    assert all("score" in item for item in resultado)


def test_aplica_o_limite_minimo_de_similaridade():
    resultado = ordenar_por_similaridade(["matemática", "frações"], VIDEOS, 0.5)

    assert [item["titulo"] for item in resultado] == ["Bem parecido"]


def test_limite_minimo_zero_inclui_todos():
    resultado = ordenar_por_similaridade(["matemática", "frações"], VIDEOS, 0.0)

    assert len(resultado) == 3


def test_nao_altera_os_dicionarios_originais():
    """Importante: o catálogo vem do banco e pode ser reutilizado depois."""
    resultado = ordenar_por_similaridade(["matemática", "frações"], VIDEOS, 0.0)

    assert "score" in resultado[0]
    assert "score" not in VIDEOS[2]


def test_lista_vazia_devolve_lista_vazia():
    assert ordenar_por_similaridade(["a"], []) == []


def test_video_sem_a_chave_tags_nao_quebra():
    resultado = ordenar_por_similaridade(["a"], [{"titulo": "órfão"}], 0.0)

    assert resultado[0]["score"] == 0.0


def test_empate_mantem_a_ordem_em_que_vieram_do_banco():
    empate = [
        {"titulo": "primeiro", "tags": ["a"]},
        {"titulo": "segundo", "tags": ["a"]},
    ]

    resultado = ordenar_por_similaridade(["a"], empate, 0.0)

    assert [item["titulo"] for item in resultado] == ["primeiro", "segundo"]


def test_score_vem_arredondado_para_duas_casas():
    resultado = ordenar_por_similaridade(["a", "b"], [{"titulo": "x", "tags": ["b", "c"]}], 0.0)

    assert resultado[0]["score"] == 0.33


def test_preserva_os_demais_campos_do_metadado():
    """A API repassa esse dict pro frontend — não pode perder campo."""
    completo = [{"titulo": "x", "tags": ["a"], "video_id": "v1", "autor": "Rafael"}]

    resultado = ordenar_por_similaridade(["a"], completo, 0.0)

    assert resultado[0]["video_id"] == "v1"
    assert resultado[0]["autor"] == "Rafael"


# ---------------------------------------------------------------------------
# recomendar_videos_por_jaccard (compatibilidade com o teste original)
# ---------------------------------------------------------------------------

def test_devolve_apenas_os_titulos():
    resultado = recomendar_videos_por_jaccard(["matemática", "frações"], VIDEOS, 0.0)

    assert resultado == ["Bem parecido", "Meio parecido", "Sem nada em comum"]
    assert all(isinstance(titulo, str) for titulo in resultado)


def test_caso_original_do_gustavo_continua_valendo():
    """Mesmo cenário do test_engine.py que veio no PR #9."""
    banco = [
        {"titulo": "Vídeo 1", "tags": ["drama", "romance"]},
        {"titulo": "Vídeo 2", "tags": ["aventura", "ação"]},
        {"titulo": "Vídeo 3", "tags": ["comédia", "ação"]},
    ]

    resultado = recomendar_videos_por_jaccard(["comédia", "ação"], banco)

    assert resultado[0] == "Vídeo 3"
    assert resultado[1] == "Vídeo 2"
    assert "Vídeo 1" not in resultado
