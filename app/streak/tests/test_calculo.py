"""
Testes do CÁLCULO do streak — app/streak/tests/test_calculo.py.

Lógica pura com o momento injetado: fuso de Brasília (o dia muda à
meia-noite de SP, não de UTC), idempotência do conceito (um dia é um dia),
quebra de sequência e os marcos 3/7/30. Sem banco, sem rede — determinístico.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.streak import calculo

FUSO = calculo.FUSO


# ---------------------------------------------------------------------------
# dia_de_hoje — o dia pertence a Brasília, não ao servidor
# ---------------------------------------------------------------------------

def test_madrugada_utc_ainda_e_o_dia_anterior_em_brasilia():
    # 02h30 de 10/01 em UTC = 23h30 de 09/01 em Brasília (UTC-3).
    momento = datetime(2026, 1, 10, 2, 30, tzinfo=timezone.utc)
    assert calculo.dia_de_hoje(momento) == date(2026, 1, 9)


def test_uma_hora_da_madrugada_utc_e_o_dia_anterior():
    momento = datetime(2026, 1, 10, 0, 0, tzinfo=timezone.utc)
    assert calculo.dia_de_hoje(momento) == date(2026, 1, 9)


def test_depois_das_03h_utc_o_dia_coincide():
    momento = datetime(2026, 1, 10, 3, 0, tzinfo=timezone.utc)
    assert calculo.dia_de_hoje(momento) == date(2026, 1, 10)


def test_datetime_sem_fuso_e_tratado_como_utc():
    momento = datetime(2026, 1, 10, 2, 0)  # naive
    assert calculo.dia_de_hoje(momento) == date(2026, 1, 9)


def test_sem_argumento_usa_o_relogio_real():
    hoje_real = datetime.now(timezone.utc).astimezone(FUSO).date()
    assert calculo.dia_de_hoje() == hoje_real


# ---------------------------------------------------------------------------
# calcular_streak — sequências, quebras e marcos
# ---------------------------------------------------------------------------

HOJE = date(2026, 6, 10)


def dias_atras(*offsets: int) -> set[date]:
    return {HOJE - timedelta(days=o) for o in offsets}


def test_sem_dias_streak_zero():
    resultado = calculo.calcular_streak(set(), HOJE)
    assert resultado["streak"] == 0
    assert resultado["estudou_hoje"] is False
    assert resultado["marco"] is None
    assert "começar" in resultado["mensagem"].lower()


def test_um_dia_estudado():
    resultado = calculo.calcular_streak(dias_atras(0), HOJE)
    assert resultado == {
        "streak": 1,
        "estudou_hoje": True,
        "marco": None,
        "mensagem": "1 dia seguidos!",
    }


def test_sequencia_de_cinco_ativa_o_marco_de_tres():
    resultado = calculo.calcular_streak(dias_atras(0, 1, 2, 3, 4), HOJE)
    assert resultado["streak"] == 5
    assert resultado["marco"] == 3
    assert resultado["mensagem"] == "3 dias seguidos!"


def test_semana_completa():
    resultado = calculo.calcular_streak(dias_atras(0, 1, 2, 3, 4, 5, 6), HOJE)
    assert resultado["streak"] == 7
    assert resultado["marco"] == 7
    assert resultado["mensagem"] == "1 semana completa!"


def test_um_mes_de_constancia_vence_os_marcos_menores():
    resultado = calculo.calcular_streak(dias_atras(*range(35)), HOJE)
    assert resultado["streak"] == 35
    assert resultado["marco"] == 30  # o marco MAIS alto é o que aparece
    assert resultado["mensagem"] == "1 mês de constância!"


def test_hoje_sem_estudar_mantem_a_sequencia_de_ontem():
    """A sequência não morre à meia-noite: dá para mantê-la durante o dia."""
    resultado = calculo.calcular_streak(dias_atras(1, 2, 3), HOJE)
    assert resultado["streak"] == 3
    assert resultado["estudou_hoje"] is False
    assert resultado["marco"] == 3  # o marco já conquistado permanece


def test_dois_dias_sem_estudar_zera():
    resultado = calculo.calcular_streak(dias_atras(2, 3, 4), HOJE)
    assert resultado["streak"] == 0
    assert resultado["marco"] is None


def test_buraco_na_sequencia_corta_o_streak():
    # hoje, ontem, anteontem ... e um buraco em -3: só 3 dias contam.
    resultado = calculo.calcular_streak(dias_atras(0, 1, 2, 4, 5, 6, 7), HOJE)
    assert resultado["streak"] == 3
    assert resultado["marco"] == 3
