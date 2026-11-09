"""
Ponto de entrada da API — Plataforma de Vídeo Educacional.

Além de registrar as rotas dos módulos (upload, status, recomendações), este
arquivo resolve a INTEGRAÇÃO com o frontend (player adaptativo):

  1. CORS liberado para desenvolvimento — sem isso, quando o player é aberto
     de outra origem (Live Server na :5500, `python -m http.server`, etc.), o
     navegador BLOQUEIA todos os `fetch` para a API e o front só mostra os
     vídeos de demonstração.
  2. `/videos` serve os arquivos gerados pela transcodificação (master.m3u8,
     playlists, segmentos .ts e thumbnails). A API devolve `hls_url` e
     `thumbnail_url` apontando para cá — antes esses arquivos existiam só no
     disco e nenhuma rota os entregava, então o player nunca dava play.
  3. `/player` serve o frontend direto do backend (mesma origem — a forma mais
     simples de rodar tudo: `uvicorn app.main:app` e abrir
     http://localhost:8000/player/).
  4. `/login` e `/cadastro` autenticam o usuário contra a tabela `usuarios`
     do Supabase (rodar supabase/schema_usuarios.sql) — é o backend da tela
     de login da branch frontend-css; o front guarda o nome em sessionStorage.
"""
import mimetypes
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.media import VIDEOS_DIR, garantir_diretorio, status_midia_completo
from app.auth.router import router as auth_router
from app.upload.router import router as upload_router
from app.status.router import router as status_router
from app.recommendations.router import router as recommendations_router

# O Starlette descobre o Content-Type pelo módulo `mimetypes` do Python, que
# não conhece .m3u8/.ts em todas as versões. Sem o tipo correto alguns players
# se recusam a carregar a playlist — registramos explicitamente.
mimetypes.add_type("application/vnd.apple.mpegurl", ".m3u8")
mimetypes.add_type("video/mp2t", ".ts")

# Caminhos ancorados na RAIZ DO REPOSITÓRIO (e não no diretório atual do
# terminal): assim `uvicorn app.main:app` funciona de qualquer cwd — antes,
# iniciar fora da raiz fazia o mount do /player não existir (404 "Not Found").
# VIDEOS_DIR vem de app/media.py, a fonte única compartilhada com o catálogo
# (é lá que o `media_pronta` de cada vídeo é calculado).
RAIZ_PROJETO = Path(__file__).resolve().parent.parent
# O frontend mora no repo projeto-a3-frontend; aponte PLAYER_DIR no .env para
# um clone dele (ex.: ../projeto-a3-frontend) para servir /player na mesma
# origem. Sem a pasta (o padrão), a API sobe normal sem o mount do player.
PLAYER_DIR = Path(
    os.getenv("PLAYER_DIR", RAIZ_PROJETO / "player-adaptativo" / "player-adaptativo")
)

app = FastAPI(
    title="Plataforma de Vídeo Educacional",
    description="API para upload, processamento e recomendação de vídeos educacionais.",
)

# CORS permissivo: o projeto é educacional e roda em localhost. Se um dia for
# publicado, troque allow_origins pelos domínios reais do front.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(upload_router)
app.include_router(status_router)
app.include_router(recommendations_router)


@app.get("/")
def health():
    return {
        "status": "ok",
        "rotas": ["/login", "/cadastro",
                  "/upload", "/status/{video_id}", "/media/{video_id}",
                  "/catalogo", "/catalogo/{video_id}",
                  "/videos/{video_id}/relacionados",
                  "/recomendacoes/{user_id}", "/trending", "/watch"],
        "player": "/player/",
        "home": "/player/home.html",
    }


@app.get("/media/{video_id}")
def media(video_id: str):
    """O que existe no disco para um vídeo (master.m3u8, thumbnail, segmentos).

    O player chama isto quando o `hls_url` falha: assim ele consegue dizer
    "o arquivo HLS não foi encontrado" (404 real) em vez de ficar num loop de
    retry, e ainda mostra quantos segmentos a transcodificação gerou — útil
    para o diagnóstico na apresentação.
    """
    return status_midia_completo(video_id)


# ---------------------------------------------------------------------------
# Arquivos estáticos — registrados DEPOIS das rotas para não engolir
# /videos/{video_id}/relacionados (no Starlette a ordem de registro importa).
# ---------------------------------------------------------------------------

# Garante que a pasta exista: StaticFiles levanta erro na inicialização se o
# diretório não existir (e em dev ela só é criada na primeira transcodificação).
garantir_diretorio()
app.mount("/videos", StaticFiles(directory=str(VIDEOS_DIR)), name="midia")

# O player mora no repo projeto-a3-frontend; o mount só acontece quando
# PLAYER_DIR (no .env) aponta para um clone dele. Sem isso, a API continua
# funcionando normal — o front roda em Live Server/http.server por conta própria.
if PLAYER_DIR.is_dir():
    app.mount("/player", StaticFiles(directory=str(PLAYER_DIR), html=True), name="player")
