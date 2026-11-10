"""
Supabase FALSO compatível com PostgREST — para o teste E2E da autenticação.

Implementa EXATAMENTE as chamadas HTTP que o supabase-py faz para a tabela
`usuarios` (o supabase-py é um cliente REST: tudo por HTTP contra /rest/v1):

  GET  /rest/v1/usuarios?select=*&email=eq.<email>&limit=1   -> array JSON
  POST /rest/v1/usuarios                                     -> array JSON (row inserida)
       em duplicidade -> 409 com corpo PostgREST (code 23505, "duplicate key")

Também expõe /__dump (rows em memória) para o relatório de evidências.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

from fastapi import FastAPI, Request, Query
from fastapi.responses import JSONResponse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # raiz do backend
from app.auth.senhas import gerar_hash  # noqa: E402

app = FastAPI(title="Supabase Falso (PostgREST)")

# "Banco" em memória — a tabela `usuarios` do schema_usuarios.sql:
# usuario_id uuid pk, nome text, email text UNIQUE, senha_hash text, criado_em timestamptz
BANCO: list[dict] = []


@app.on_event("startup")
def seed() -> None:
    """Mesmo usuário de teste do supabase/schema_usuarios.sql."""
    BANCO.append(
        {
            "usuario_id": str(uuid.uuid4()),
            "nome": "Usuário de Teste",
            "email": "teste@edustream.com",
            "senha_hash": gerar_hash("senha12345"),
            "criado_em": "2026-11-10T00:00:00+00:00",
        }
    )


def _email_do_filtro(valor: str) -> str:
    # PostgREST manda "eq.<valor>"
    return valor.removeprefix("eq.").strip().lower()


@app.get("/rest/v1/usuarios")
def selecionar(
    select: str = Query("*"),
    email: str | None = Query(None),
    limit: int | None = Query(None),
):
    achados = BANCO
    if email is not None:
        alvo = _email_do_filtro(email)
        achados = [r for r in achados if r["email"] == alvo]
    if limit is not None:
        achados = achados[:limit]
    return JSONResponse(achados)


@app.post("/rest/v1/usuarios")
async def inserir(request: Request):
    payload = await request.json()
    linhas = payload if isinstance(payload, list) else [payload]

    for linha in linhas:
        email = linha["email"].strip().lower()
        if any(r["email"] == email for r in BANCO):
            # Corpo de erro do PostgREST em duplicidade de unique (23505) —
            # é exatamente o formato que app/auth/usuarios.py espera tratar.
            return JSONResponse(
                {
                    "code": "23505",
                    "message": 'duplicate key value violates unique constraint "usuarios_email_unico"',
                    "details": "Key (email)=(%s) already exists." % email,
                    "hint": None,
                },
                status_code=409,
            )

    inseridos = []
    for linha in linhas:
        registro = {
            "usuario_id": str(uuid.uuid4()),
            "nome": linha["nome"],
            "email": linha["email"].strip().lower(),
            "senha_hash": linha["senha_hash"],
            "criado_em": "2026-11-10T03:00:00+00:00",
        }
        BANCO.append(registro)
        inseridos.append(registro)
    return JSONResponse(inseridos)


@app.get("/__dump")
def dump():
    """Evidência: linhas gravadas (inclusive a senha_hash armazenada)."""
    return JSONResponse(BANCO)


@app.post("/__reset")
def reset():
    BANCO.clear()
    seed()
    return {"ok": True}
