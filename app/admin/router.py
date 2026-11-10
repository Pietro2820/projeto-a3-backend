"""
Rotas de administração — só o papel `admin` chega aqui (guard papeis.admin).

  POST /admin/professores   cria a conta do professor (senha temporária +
                            deve_trocar_senha=true)
  GET  /admin/usuarios      lista os usuários SEM senha_hash

Por que professor só nasce aqui: cadastro público vira estudante fixo
(app/auth/usuarios.py); a promessa "somente o admin cria professor" é o
guard desta rota — não existe outro caminho de escrita na tabela.

Fluxo combinado com o usuário:
  1. admin cria a conta com um e-mail padrão e senha temporária;
  2. passa as credenciais ao professor (em pessoa — sem e-mail na v1);
  3. o 1º acesso do professor é bloqueado até POST /trocar-senha.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.admin.schemas import NovoProfessorRequest
from app.auth import papeis, usuarios

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/professores", status_code=201)
async def criar_professor(
    dados: NovoProfessorRequest,
    admin_atual: dict = Depends(papeis.admin),
):
    """Cria uma conta de professor — nasce bloqueada para o 1º acesso."""
    try:
        professor = usuarios.criar_professor(
            dados.nome, dados.email, dados.senha_temporaria
        )
    except usuarios.EmailDuplicadoError:
        raise HTTPException(
            status_code=409,
            detail="Este e-mail já tem uma conta. Use outro e-mail.",
        )
    except Exception as exc:  # .env, migração 002 ausente, rede...
        raise HTTPException(
            status_code=503,
            detail=(
                "Banco de dados indisponível. Verifique SUPABASE_URL/"
                "SUPABASE_KEY no .env e rode supabase/schema_usuarios.sql + "
                "supabase/migracao_002_papeis_streak.sql no SQL Editor."
            ),
        ) from exc

    logger.info(
        "Admin %s criou o professor %s",
        admin_atual.get("usuario_id"),
        professor["email"],
    )
    # criar_professor já devolve o recorte público (usuarios.usuario_publico
    # é aplicado no store) — projetar de novo perderia a flag de 1º acesso.
    return {
        "usuario": professor,
        "mensagem": (
            "Conta de professor criada. Passa as credenciais a ele: no "
            "primeiro acesso será exigida a troca da senha."
        ),
    }


@router.get("/usuarios")
async def listar_usuarios(admin_atual: dict = Depends(papeis.admin)):
    """Lista os usuários da plataforma — SEM senha_hash (projeção do store)."""
    try:
        usuarios_publicos = usuarios.listar_todos()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Banco de dados indisponível. Verifique SUPABASE_URL/"
                "SUPABASE_KEY no .env e rode as migrações no SQL Editor."
            ),
        ) from exc

    return {"usuarios": usuarios_publicos, "total": len(usuarios_publicos)}
