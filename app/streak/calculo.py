"""
Cálculo da sequência de estudos (streak) — app/streak/calculo.py.

REGRA COMBINADA COM O USUÁRIO: um dia de estudo é a CONCLUSÃO de um vídeo
(o player avisa o fim da exibição; o servidor grava 1 registro por dia).

Este módulo é LÓGICA PURA — nada de banco, nada de rede, nada de "agora"
escondido: toda função recebe o momento por parâmetro (agora/hoje). É o
que torna os testes determinísticos — dá para simular madrugada de
Brasília, viradas de dia e sequências de semanas sem dormir na frente do
terminal.

FUSO: America/Sao_Paulo (UTC-3). O dia pertence a QUEM ESTUDA, não ao
servidor: 01h00 de 11/11 em UTC ainda é 10/11 em Brasília — o aluno que
termina a aula à meia-noite não perde o dia por diferença de relógio.

IDEMPOTÊNCIA: a tabela dias_estudo tem PK (usuario_id, dia) e o registro
usa upsert com ignore_duplicates — concluir 5 vídeos no mesmo dia conta
UM dia. O cliente NÃO manda data: o dia é sempre calculado aqui.

MARCOS (decisão do usuário): 3, 7 e 30 dias, sempre no tom motivacional —
nunca punitivo. Não existe "streak quebrado = vergonha": streak 0 é só
o começo.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

FUSO = ZoneInfo("America/Sao_Paulo")

# Marcos em dias → mensagem do card. A ordem é do MAIOR para o menor:
# quem tem 35 dias merece o "1 mês", não o "3 dias".
MARCOS = (
    (30, "1 mês de constância!"),
    (7, "1 semana completa!"),
    (3, "3 dias seguidos!"),
)


def dia_de_hoje(agora: datetime | None = None) -> date:
    """A data de HOJE no fuso do aluno (Brasília), a partir de um instante UTC."""
    momento = agora or datetime.now(timezone.utc)
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)
    return momento.astimezone(FUSO).date()


def calcular_streak(dias: set[date], hoje: date) -> dict:
    """Streak a partir do conjunto de dias estudados.

    Definição: a sequência conta os dias CONSECUTivos terminando em hoje —
    ou em ONTEM, quando hoje ainda não foi estudado (a sequência não morre
    à meia-noite: o aluno tem o dia inteiro para mantê-la). Dois dias sem
    estudar zera de verdade.
    """
    if hoje in dias:
        estudou_hoje = True
        ponta = hoje
    elif (hoje - timedelta(days=1)) in dias:
        estudou_hoje = False
        ponta = hoje - timedelta(days=1)
    else:
        return {
            "streak": 0,
            "estudou_hoje": False,
            "marco": None,
            "mensagem": "Assista a um vídeo para começar sua sequência!",
        }

    streak = 0
    dia = ponta
    while dia in dias:
        streak += 1
        dia -= timedelta(days=1)

    for limite, mensagem in MARCOS:
        if streak >= limite:
            return {
                "streak": streak,
                "estudou_hoje": estudou_hoje,
                "marco": limite,
                "mensagem": mensagem,
            }

    return {
        "streak": streak,
        "estudou_hoje": estudou_hoje,
        "marco": None,
        "mensagem": f"{streak} dia{'s' if streak > 1 else ''} seguidos!",
    }
