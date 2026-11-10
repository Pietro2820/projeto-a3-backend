"""
Acesso à tabela `dias_estudo` (migração 002) — app/streak/repo.py.

Duas operações, as únicas de que o streak precisa:

  * registrar_dia(usuario_id, agora) — upsert com ignore_duplicates na PK
    (usuario_id, dia): concluir N vídeos no mesmo dia grava UMA linha;
  * dias_estudados(usuario_id) — conjunto de datas para o cálculo.

O cliente NUNCA manda o dia: ele é calculado no fuso de Brasília por
app/streak/calculo.py. E o repo não sabe o que é papel — quem garante que
só estudante chega aqui são os guardas (papeis.estudante no router e o
filtro do POST /watch).
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from app.database.client import get_client
from app.streak.calculo import dia_de_hoje

TABLE = "dias_estudo"


def registrar_dia(usuario_id: str, agora: datetime | None = None) -> str:
    """Grava (idempotente) o dia de estudo. Devolve o dia gravado.

    ignore_duplicates=True é a idempotência do banco: segunda chamada no
    mesmo dia não cria linha nem erro — a primeira vence.
    """
    client = get_client()
    dia = dia_de_hoje(agora)
    (
        client.table(TABLE)
        .upsert(
            {"usuario_id": usuario_id, "dia": dia.isoformat()},
            on_conflict="usuario_id,dia",
            ignore_duplicates=True,
        )
        .execute()
    )
    return dia.isoformat()


def dias_estudados(usuario_id: str) -> set[date]:
    """Todos os dias que o aluno estudou (conjunto de date)."""
    client = get_client()
    response = (
        client.table(TABLE)
        .select("dia")
        .eq("usuario_id", usuario_id)
        .execute()
    )
    dias: set[date] = set()
    for linha in response.data:
        bruto = linha.get("dia")
        if isinstance(bruto, date):
            dias.add(bruto)
        elif isinstance(bruto, str) and bruto:
            dias.add(date.fromisoformat(bruto[:10]))
    return dias


def agora_utc() -> datetime:
    """Agora em UTC — separado para o teste injetar quando precisar."""
    return datetime.now(timezone.utc)
