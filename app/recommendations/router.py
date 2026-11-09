"""
Rotas de recomendação — contrato #6 da documentação técnica.

    GET  /videos/{video_id}/relacionados   vídeos similares (Jaccard sobre tags)
    GET  /recomendacoes/{user_id}          recomendações personalizadas
    GET  /trending                         mais assistidos
    POST /watch                            registra uma visualização

Antes deste módulo o player (js/api.js) chamava essas quatro rotas e recebia
404 — a sidebar e os "relacionados" só funcionavam com o DEMO_VIDEOS fixo do
frontend. O motor de Jaccard do Gustavo existia, mas não estava ligado na API.

Decisões de projeto:
  * A resposta segue o formato de metadados do contrato #2 (video_id, titulo,
    tags, categoria, autor, criado_em) e acrescenta o que o player precisa para
    desenhar o card: views, score, hls_url e thumbnail_url.
  * Erro de banco vira 503 com mensagem legível, nunca 500 com stacktrace.
    Isso importa porque o projeto ainda roda sem credenciais do Supabase: a
    aplicação continua de pé e diz o que falta.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.database.videos import buscar_video, listar_videos
from app.media import base_publica_s3, master_existe, origem_midia
from app.recommendations import views_store
from app.recommendations.engine import normalizar_tags, ordenar_por_similaridade

logger = logging.getLogger(__name__)

router = APIRouter(tags=["recomendacoes"])

# Corte de similaridade: abaixo disso os vídeos não têm nada em comum de útil.
LIMITE_MINIMO_SIMILARIDADE = 0.1


class WatchRequest(BaseModel):
    """Corpo do POST /watch."""

    video_id: str = Field(min_length=1, description="UUID do vídeo assistido")
    user_id: str | None = Field(
        default=None, description="Opcional — alimenta as recomendações pessoais"
    )


def _executar_no_banco(operacao, *args, **kwargs):
    """Centraliza o tratamento de erro de acesso ao Supabase.

    `get_client()` levanta RuntimeError quando faltam SUPABASE_URL/SUPABASE_KEY.
    Sem este wrapper isso viraria um 500 genérico na cara do usuário.
    """
    try:
        return operacao(*args, **kwargs)
    except RuntimeError as exc:
        logger.error("Banco indisponível: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=(
                "Banco de dados não configurado. Preencha SUPABASE_URL e "
                "SUPABASE_KEY no .env (modelo em .env.example)."
            ),
        ) from exc
    except HTTPException:
        raise
    except Exception as exc:  # rede fora, tabela ausente, resposta inesperada
        logger.exception("Falha inesperada ao consultar vídeos")
        raise HTTPException(
            status_code=502, detail=f"Falha ao consultar o banco: {exc}"
        ) from exc


def _views_seguras(video_id: str) -> int:
    """Contagem de views que nunca derruba a rota.

    Sem Redis (dev no Windows sem Docker, por exemplo), o contador de
    visualizações está indisponível — o catálogo continua vindo do banco,
    só que com 0 views, em vez de virar um erro 500 na cara do player.
    """
    try:
        return views_store.contar_visualizacoes(video_id)
    except Exception as exc:
        logger.warning("Contador de visualizações indisponível: %s", exc)
        return 0


def _url_absoluta(request: Request | None, caminho: str) -> str:
    """Transforma '/videos/x/master.m3u8' em URL absoluta usando a origem da
    requisição (ex: 'http://localhost:8000/videos/x/master.m3u8').

    Por quê: o player roda em origem própria quando aberto via Live Server ou
    outro host. Com URL relativa, o navegador resolve contra a origem do
    FRONT (que não tem esses arquivos) e o vídeo/thumbnail dá 404. URL
    absoluta funciona nas duas montagens (mesma origem ou CORS).
    """
    if request is None:
        return caminho
    base = str(request.base_url).rstrip("/")
    return f"{base}{caminho}"


def _url_midia(request: Request | None, video_id: str, arquivo: str,
               origem: str | None) -> str | None:
    """URL de um arquivo de mídia do vídeo, na origem onde ele realmente está.

    `origem` vem de `media.origem_midia()`:
      * "s3"    — o HLS está no bucket público (Supabase Storage): devolve a
                  URL pública ABSOLUTA. É o caso que estava quebrado — a API
                  montava sempre o caminho local, o bucket não estava em lugar
                  nenhum da resposta e o player recebia um 404 do próprio
                  backend para um vídeo que existia no Storage.
      * "local" / None — caminho servido pelo mount `/videos` da API, como
                  antes (dev sem S3, testes e contrato antigo inalterados).
    """
    if origem == "s3":
        return f"{base_publica_s3()}/videos/{video_id}/{arquivo}"
    return _url_absoluta(request, f"/videos/{video_id}/{arquivo}")


def _enriquecer(video: dict, score: float | None = None,
                request: Request | None = None) -> dict:
    """Acrescenta ao metadado do contrato #2 o que o player usa no card.

    `media_pronta` diz se o master.m3u8 EXISTE (no disco da API ou no bucket
    público). `origem_midia` diz ONDE ele está — "local" ou "s3" — e é o que
    determina se `hls_url` aponta para a API ou para o Supabase Storage. Sem
    essas duas informações o player não tem como saber se o arquivo está lá, e
    ficava num loop de retry num 404 quando a transcodificação não tinha
    gerado a mídia (ou quando ela estava só no bucket).
    """
    video_id = video.get("video_id")
    origem = origem_midia(video_id) if video_id else None
    resposta = {
        **video,
        "tags": normalizar_tags(video.get("tags")),
        "views": _views_seguras(video_id) if video_id else 0,
        "media_pronta": origem is not None,
        "origem_midia": origem,
        "hls_url": (
            _url_midia(request, video_id, "master.m3u8", origem)
            if video_id else None
        ),
        "thumbnail_url": (
            _url_midia(request, video_id, "thumbnail.jpg", origem)
            if video_id else None
        ),
    }
    if score is not None:
        resposta["score"] = score
    return resposta


def _catalogo_pronto() -> list[dict]:
    """Vídeos que já terminaram de transcodificar (só esses dão play)."""
    return _executar_no_banco(listar_videos, status="completed")


# ---------------------------------------------------------------------------
# GET /videos/{video_id}/relacionados
# ---------------------------------------------------------------------------

@router.get("/videos/{video_id}/relacionados")
def videos_relacionados(
    request: Request,
    video_id: str,
    limite: int = Query(default=5, ge=1, le=50),
):
    """Vídeos com tags parecidas com as do vídeo informado."""
    atual = _executar_no_banco(buscar_video, video_id)
    if atual is None:
        raise HTTPException(status_code=404, detail="video_id não encontrado")

    catalogo = _catalogo_pronto()
    parecidos = ordenar_por_similaridade(
        atual.get("tags"),
        [video for video in catalogo if video.get("video_id") != video_id],
        LIMITE_MINIMO_SIMILARIDADE,
    )

    return {
        "video_id": video_id,
        "relacionados": [
            _enriquecer(video, score=video["score"], request=request)
            for video in parecidos[:limite]
        ],
    }


# ---------------------------------------------------------------------------
# GET /recomendacoes/{user_id}
# ---------------------------------------------------------------------------

@router.get("/recomendacoes/{user_id}")
def recomendacoes_personalizadas(
    request: Request,
    user_id: str,
    limite: int = Query(default=5, ge=1, le=50),
):
    """Recomenda a partir das tags do que o usuário já assistiu."""
    historico = views_store.historico_do_usuario(user_id)

    if not historico:
        return {
            "user_id": user_id,
            "recomendacoes": [],
            "motivo": "usuário ainda não assistiu nenhum vídeo",
        }

    assistidos = []
    for assistido_id in historico:
        video = _executar_no_banco(buscar_video, assistido_id)
        if video is not None:
            assistidos.append(video)

    # O "perfil" do usuário é a união das tags do que ele assistiu.
    tags_do_usuario: list[str] = []
    for video in assistidos:
        tags_do_usuario.extend(normalizar_tags(video.get("tags")))

    ja_vistos = set(historico)
    catalogo = [
        video
        for video in _catalogo_pronto()
        if video.get("video_id") not in ja_vistos
    ]

    sugeridos = ordenar_por_similaridade(
        tags_do_usuario, catalogo, LIMITE_MINIMO_SIMILARIDADE
    )

    return {
        "user_id": user_id,
        "recomendacoes": [
            _enriquecer(video, score=video["score"], request=request)
            for video in sugeridos[:limite]
        ],
    }


# ---------------------------------------------------------------------------
# GET /trending
# ---------------------------------------------------------------------------

@router.get("/trending")
def trending(request: Request, limite: int = Query(default=10, ge=1, le=50)):
    """Mais assistidos, segundo o contador em Redis."""
    try:
        ranking = views_store.mais_assistidos(limite)
    except Exception as exc:  # Redis fora do ar não pode derrubar o player
        logger.warning("Contador de visualizações indisponível: %s", exc)
        return {
            "trending": [],
            "motivo": "contador de visualizações indisponível (Redis fora do ar?)",
        }

    if not ranking:
        return {"trending": [], "motivo": "nenhuma visualização registrada ainda"}

    videos = []
    for video_id, views in ranking:
        video = _executar_no_banco(buscar_video, video_id)
        if video is None:
            # O vídeo pode ter sido apagado do banco; o ranking não pode quebrar.
            logger.warning("video_id %s no ranking mas não está no banco", video_id)
            continue
        enriquecido = _enriquecer(video, request=request)
        enriquecido["views"] = views
        videos.append(enriquecido)

    return {"trending": videos}


# ---------------------------------------------------------------------------
# GET /catalogo e GET /catalogo/{video_id} — usados pela home do player
# ---------------------------------------------------------------------------

@router.get("/catalogo")
def catalogo(
    request: Request,
    q: str = Query(default="", max_length=100, description="Busca por título ou tag"),
    limite: int = Query(default=50, ge=1, le=200),
    prontos: bool = Query(
        default=False,
        description="true = só vídeos com master.m3u8 no disco (dá play de verdade)",
    ),
):
    """Catálogo de vídeos prontos para assistir (status='completed').

    É o endpoint que alimenta a home do player (grade estilo YouTube) e a
    busca do topo da página. Sem Redis o catálogo continua funcionando —
    só as contagens de views ficam em zero.

    `prontos=true` filtra também pela MÍDIA (arquivos HLS no disco). A home do
    player usa isso para não exibir card de vídeo que não toca; o padrão fica
    `false` para não mudar o comportamento de quem já consome a rota.
    """
    videos = _catalogo_pronto()

    if prontos:
        videos = [video for video in videos if master_existe(video.get("video_id"))]

    termo = q.strip().lower()
    if termo:
        videos = [
            video for video in videos
            if termo in str(video.get("titulo", "")).lower()
            or any(termo in tag for tag in normalizar_tags(video.get("tags")))
        ]

    videos.sort(
        key=lambda video: (-_views_seguras(video.get("video_id")),
                           str(video.get("titulo", "")))
    )

    return {
        "videos": [_enriquecer(video, request=request) for video in videos[:limite]],
        "total": len(videos),
    }


@router.get("/catalogo/{video_id}")
def catalogo_detalhe(request: Request, video_id: str):
    """Metadado enriquecido de UM vídeo (a home navega para ?video={id})."""
    video = _executar_no_banco(buscar_video, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="video_id não encontrado")
    return _enriquecer(video, request=request)


# ---------------------------------------------------------------------------
# POST /watch
# ---------------------------------------------------------------------------

@router.post("/watch")
def registrar_watch(payload: WatchRequest):
    """Registra uma visualização (alimenta /trending e /recomendacoes)."""
    try:
        views = views_store.registrar_visualizacao(payload.video_id, payload.user_id)
    except Exception as exc:  # Redis fora do ar não pode derrubar o player
        logger.exception("Falha ao registrar visualização no Redis")
        raise HTTPException(
            status_code=503, detail="Contador de visualizações indisponível"
        ) from exc

    return {
        "video_id": payload.video_id,
        "views": views,
        "registrado": True,
    }
