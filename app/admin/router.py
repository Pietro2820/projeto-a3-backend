"""
Rotas de administração — só o papel `admin` chega aqui (guard papeis.admin).

  POST /admin/professores   cria a conta do professor (senha temporária +
                            deve_trocar_senha=true)
  GET  /admin/usuarios      lista os usuários SEM senha_hash
  GET  /admin/resumo        números da plataforma (visão geral do painel)
  GET  /admin/videos        catálogo completo com dono/status/views — é a
                            base da moderação: o admin apaga vídeo de
                            qualquer professor via DELETE /videos/{video_id}
                            (o guard _garantir_dono deixa admin passar)

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
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException

from app.admin.schemas import NovoProfessorRequest
from app.auth import papeis, usuarios
from app.database import videos as videos_store
from app.streak import repo as repo_streak

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


def _views_seguras(video_id: str) -> int:
    """Views best-effort: sem Redis a moderação segue de pé com 0."""
    try:
        from app.recommendations import views_store
        return views_store.contar_visualizacoes(video_id)
    except Exception as exc:  # Redis fora do ar nunca derruba o painel
        logger.warning("Views indisponíveis para %s: %s", video_id, exc)
        return 0


@router.get("/resumo")
async def resumo(admin_atual: dict = Depends(papeis.admin)):
    """Números da plataforma para a VISÃO GERAL do painel do admin.

    Tudo vem das MESMAS fontes dos demais painéis: usuarios.listar_todos
    (papéis), videos_store.listar_videos (status de processamento) e o repo
    do streak (dias_estudo). O bloco de estudos é best-effort: sem Supabase
    ele sai como null e o front omite o cartão — o resto do resumo segue
    de pé. Nenhum dado individual de aluno sai daqui, só totais.
    """
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

    try:
        todos_videos = videos_store.listar_videos()
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="Banco de dados indisponível."
        ) from exc

    por_papel = Counter(
        (u.get("role") or "estudante") for u in usuarios_publicos
    )
    por_status = Counter(
        (v.get("status") or "pending") for v in todos_videos
    )

    estudos = None
    try:
        estudos = repo_streak.resumo_dias()
    except Exception as exc:  # dias_estudo precisa de Supabase — sem ele, null
        logger.warning("Resumo de estudos indisponível: %s", exc)

    return {
        "usuarios": {
            "total": len(usuarios_publicos),
            "admins": por_papel.get("admin", 0),
            "professores": por_papel.get("professor", 0),
            "estudantes": por_papel.get("estudante", 0),
        },
        "videos": {
            "total": len(todos_videos),
            "prontos": por_status.get("completed", 0),
            "processando": por_status.get("processing", 0),
            "pendentes": por_status.get("pending", 0),
            "falhos": por_status.get("failed", 0),
        },
        "estudos": estudos,
    }


@router.get("/videos")
async def listar_videos(admin_atual: dict = Depends(papeis.admin)):
    """Catálogo COMPLETO para a moderação — dono, status bruto e views.

    O acréscimo sobre o catálogo público é o dono (nome/e-mail resolvidos
    contra a tabela usuarios, projeção pública — sem senha_hash) e o status
    do pipeline. Apagar é o DELETE /videos/{video_id} de sempre: o admin
    passa no guard de dono por herança de papel — a moderação não cria
    segunda rota de escrita, reaproveita a que já é vigiada.
    """
    try:
        todos_videos = videos_store.listar_videos()
        pessoas = {
            u["usuario_id"]: u for u in usuarios.listar_todos()
        }
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Banco de dados indisponível. Verifique SUPABASE_URL/"
                "SUPABASE_KEY no .env e rode as migrações no SQL Editor."
            ),
        ) from exc

    enriquecidos = []
    for video in todos_videos:
        dono = pessoas.get(video.get("professor_id"))
        enriquecidos.append(
            {
                **video,
                "dono": (
                    {"nome": dono.get("nome"), "email": dono.get("email")}
                    if dono
                    else None  # vídeo antigo de professor apagado
                ),
                "views": _views_seguras(video.get("video_id", "")),
            }
        )

    return {"videos": enriquecidos, "total": len(enriquecidos)}
