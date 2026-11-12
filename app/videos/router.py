"""
Propriedade de vídeo — painel "Meus vídeos" do professor (migração 002).

    GET    /meus-videos        vídeos DO professor da sessão (qualquer status)
    PATCH  /videos/{video_id}  edita metadados — SÓ o dono (admin herda)
    DELETE /videos/{video_id}  apaga vídeo e arquivos — SÓ o dono (admin herda)

Por que este módulo existe
--------------------------
O upload é somente-professor (app/upload/router.py) e o DONO do vídeo é a
SESSÃO que o fez — registro['professor_id'], nunca o campo `autor` do
formulário, que é só nome de exibição no catálogo. Este router fecha esse
ciclo: listar, editar e apagar o próprio vídeo, com a regra de propriedade
verificada NO SERVIDOR em toda chamada (o front é só conveniência; curl é
o atacante real — js/dono.js esconde o botão, mas quem garante a regra é
o guard aqui).

Consumidores (repo projeto-a3-frontend):
  * js/meus-videos.js  — GET /meus-videos, PATCH e DELETE do painel;
  * js/painel-admin.js — moderação: GET /admin/videos + DELETE (o admin
    passa na checagem de dono por herança de papel).

Regras de código HTTP (as mesmas do restante da API):
  401 — sem sessão (cookie ausente, inválido ou expirado);
  403 — com sessão, mas sem permissão (o vídeo pertence a outro professor);
  404 — video_id inexistente;
  422 — corpo inválido (título vazio, nada para atualizar);
  503/502 — banco indisponível, com mensagem legível (nunca 500 com
    stacktrace na cara do usuário).
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth import papeis
from app.database.videos import (
    atualizar_video,
    buscar_video,
    listar_por_professor,
    remover_video,
)
from app.media import VIDEOS_DIR
from app.recommendations import views_store
from app.recommendations.engine import normalizar_tags
from app.upload.router import UPLOAD_DIR, parse_tags

logger = logging.getLogger(__name__)

router = APIRouter(tags=["videos"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _executar_no_banco(operacao, *args, **kwargs):
    """Centraliza o tratamento de erro de acesso ao banco (padrão do projeto).

    O fallback local de app/database/videos.py cobre o dev sem Supabase, mas
    um banco REAL fora do ar (rede, RLS, tabela ausente) ainda pode estourar
    exceção — isso aqui traduz para 503/502 legíveis em vez de 500 genérico.
    """
    try:
        return operacao(*args, **kwargs)
    except HTTPException:
        raise
    except RuntimeError as exc:  # credenciais ausentes no get_client()
        logger.error("Banco indisponível: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=(
                "Banco de dados não configurado. Preencha SUPABASE_URL e "
                "SUPABASE_KEY no .env (modelo em .env.example)."
            ),
        ) from exc
    except Exception as exc:  # rede fora, tabela ausente, resposta inesperada
        logger.exception("Falha inesperada ao acessar vídeos no banco")
        raise HTTPException(
            status_code=502, detail=f"Falha ao consultar o banco: {exc}"
        ) from exc


def _views_seguras(video_id: str) -> int:
    """Contagem de views que nunca derruba a rota (mesmo padrão do /catalogo).

    Sem Redis o contador fica indisponível — o painel continua listando os
    vídeos, só que com 0 views, em vez de virar erro.
    """
    try:
        return views_store.contar_visualizacoes(video_id)
    except Exception as exc:
        logger.warning("Contador de visualizações indisponível: %s", exc)
        return 0


def _garantir_dono(video: dict, usuario: dict) -> None:
    """Regra de propriedade: dono passa, ADMIN passa, qualquer outro leva 403.

    O admin gerencia a plataforma inteira (moderação do painel-admin apaga
    vídeo de qualquer professor). professor_id ausente (vídeo LEGADO, subido
    antes da migração 002) não pertence a ninguém — só o admin mexe nele.
    """
    if usuario.get("role") == "admin":
        return
    if video.get("professor_id") != usuario.get("usuario_id"):
        raise HTTPException(
            status_code=403,
            detail="Este vídeo pertence a outro professor.",
        )


def _id_seguro_para_arquivos(video_id: str) -> bool:
    """video_id precisa ser UM componente de caminho antes de virar pasta.

    O cleanup do DELETE junta video_id a UPLOAD_DIR/VIDEOS_DIR — um id como
    "../../etc" não pode nunca chegar ao shutil. A rota já devolve 404 antes
    (o banco nunca teria esse id), mas a defesa fica aqui também.
    """
    return bool(video_id) and Path(video_id).name == video_id


def _limpar_arquivos(video_id: str) -> dict:
    """Apaga as pastas de ARQUIVOS do vídeo — best-effort, nunca falha a rota.

    O metadado já saiu do banco quando esta função roda; sobrar arquivo no
    disco é desperdício, não erro. `uploads/{id}/original.ext` é o original
    que o worker Celery consome e `videos/{id}/` guarda o HLS gerado
    (master.m3u8, playlists, segmentos .ts e thumbnail.jpg).
    """
    removidos = {"uploads": False, "midia": False}
    if not _id_seguro_para_arquivos(video_id):
        return removidos

    pastas = {
        "uploads": Path(UPLOAD_DIR) / video_id,
        "midia": Path(VIDEOS_DIR) / video_id,
    }
    for chave, pasta in pastas.items():
        try:
            if pasta.is_dir():
                shutil.rmtree(pasta)
                removidos[chave] = True
        except OSError as exc:
            logger.warning(
                "Não foi possível apagar a pasta %s do vídeo %s: %s",
                chave, video_id, exc,
            )
    return removidos


def _item_do_painel(video: dict) -> dict:
    """Metadado pronto para a tabela do painel (js/meus-videos.js).

    Acrescenta `views` (Redis indisponível → 0) e normaliza `tags` para
    lista — o mesmo formato do /catalogo, então qualquer componente do front
    que já sabe desenhar card do catálogo desenha linha daqui também.
    """
    video_id = video.get("video_id", "")
    return {
        **video,
        "tags": normalizar_tags(video.get("tags")),
        "views": _views_seguras(video_id) if video_id else 0,
    }


# ---------------------------------------------------------------------------
# GET /meus-videos — a lista do painel do professor
# ---------------------------------------------------------------------------

@router.get("/meus-videos")
def meus_videos(
    request: Request,
    professor_atual: dict = Depends(papeis.professor_ou_admin),
):
    """Vídeos DO professor da sessão — qualquer status, com views.

    O filtro é NO BANCO (listar_por_professor), nunca no cliente: quem
    chama não consegue "ver por descuido" vídeo de outra pessoa. Estudante
    recebe 403 (o guard já responde) — o painel de vídeos é de professor.
    """
    videos = _executar_no_banco(listar_por_professor, professor_atual["usuario_id"])
    itens = [_item_do_painel(video) for video in videos]
    return {"videos": itens, "total": len(itens)}


# ---------------------------------------------------------------------------
# PATCH /videos/{video_id} — editar metadados (só o dono)
# ---------------------------------------------------------------------------

class VideoUpdateRequest(BaseModel):
    """Corpo do PATCH /videos/{video_id} — tudo opcional (atualização parcial).

    `tags` aceita os dois formatos que o front manda: a STRING do formulário
    de edição ("educação, matemática") ou lista pronta. O STATUS não está
    aqui DE PROPÓSITO — o ciclo de vida (pending/processing/completed/
    failed) é do pipeline Celery, e um PATCH do painel não pode pular a fila.
    """

    titulo: str | None = None
    descricao: str | None = None
    tags: str | list[str] | None = None
    categoria: str | None = None


def _tags_normalizadas(tags) -> list[str]:
    """String separada por vírgula OU lista → lista limpa (mesma do upload)."""
    if isinstance(tags, str):
        return parse_tags(tags)
    return [str(tag).strip() for tag in tags if str(tag).strip()]


@router.patch("/videos/{video_id}")
def editar_video(
    video_id: str,
    dados: VideoUpdateRequest,
    request: Request,
    usuario: dict = Depends(papeis.autenticado),
):
    """Edita metadados do vídeo — SÓ o dono (ou admin).

    Campos ausentes no corpo ficam como estão (merge); campos enviados
    substituem. O `status` enviado por cliente mal-intencionado é IGNORADO
    (não está no modelo — ver VideoUpdateRequest).
    """
    video = _executar_no_banco(buscar_video, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="video_id não encontrado")

    _garantir_dono(video, usuario)

    campos: dict = {}
    if dados.titulo is not None:
        titulo = dados.titulo.strip()
        if not titulo:
            raise HTTPException(
                status_code=422, detail="O título é obrigatório."
            )
        campos["titulo"] = titulo
    if dados.descricao is not None:
        campos["descricao"] = dados.descricao.strip()
    if dados.categoria is not None:
        campos["categoria"] = dados.categoria.strip()
    if dados.tags is not None:
        campos["tags"] = _tags_normalizadas(dados.tags)

    if not campos:
        raise HTTPException(
            status_code=422,
            detail="Nada para atualizar — envie ao menos um campo "
                   "(titulo, descricao, tags ou categoria).",
        )

    atualizado = _executar_no_banco(atualizar_video, video_id, campos)
    if atualizado is None:  # apagado entre o buscar e o atualizar (raro)
        raise HTTPException(status_code=404, detail="video_id não encontrado")

    return {
        "video": _item_do_painel(atualizado),
        "mensagem": "Vídeo atualizado com sucesso.",
    }


# ---------------------------------------------------------------------------
# DELETE /videos/{video_id} — apagar (só o dono; admin modera qualquer um)
# ---------------------------------------------------------------------------

@router.delete("/videos/{video_id}")
def apagar_video(
    video_id: str,
    request: Request,
    usuario: dict = Depends(papeis.autenticado),
):
    """Apaga o vídeo — metadados no banco + arquivos no disco (best-effort).

    A ação não tem volta (o front pede confirm antes). O admin pode apagar
    vídeo de qualquer professor — é a moderação do painel-admin.
    """
    video = _executar_no_banco(buscar_video, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="video_id não encontrado")

    _garantir_dono(video, usuario)

    apagado = _executar_no_banco(remover_video, video_id)
    if not apagado:  # sumiu entre o buscar e o remover (raro)
        raise HTTPException(status_code=404, detail="video_id não encontrado")

    limpeza = _limpar_arquivos(video_id)
    logger.info(
        "Vídeo %s apagado por %s (arquivos removidos: uploads=%s midia=%s)",
        video_id, usuario.get("email", "?"),
        limpeza["uploads"], limpeza["midia"],
    )

    return {
        "video_id": video_id,
        "arquivos_removidos": limpeza,
        "mensagem": "Vídeo apagado com sucesso.",
    }
