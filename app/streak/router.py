"""
Rota do streak — GET /streak/meu (app/streak/router.py).

PRIVACIDADE (decisão do usuário): o streak é EXCLUSIVO do estudante —
professor e admin recebem 403. O professor não vigia a sequência do aluno,
e nem tem a própria: a rota existe só para o card da home do aluno.

O cálculo é 100% servidor: dias vem da tabela dias_estudo e a data de hoje
do fuso America/Sao_Paulo é calculada aqui. O cliente não manda nada além
do cookie — não existe forma de "marcar estudado" por esta rota (o dia só
entra pela conclusão real de vídeo, POST /watch com concluido=true).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.auth import papeis
from app.streak import calculo, repo

logger = logging.getLogger(__name__)

router = APIRouter(tags=["streak"])


@router.get("/streak/meu")
async def meu_streak(registro: dict = Depends(papeis.estudante)):
    """Card de sequência do aluno: streak, marco (3/7/30) e mensagem."""
    try:
        dias = repo.dias_estudados(registro["usuario_id"])
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Banco de dados indisponível. Verifique SUPABASE_URL/"
                "SUPABASE_KEY no .env e rode supabase/migracao_002_papeis_"
                "streak.sql no SQL Editor."
            ),
        ) from exc

    resultado = calculo.calcular_streak(dias, calculo.dia_de_hoje())
    resultado["registrado_hoje"] = resultado["estudou_hoje"]
    return resultado
