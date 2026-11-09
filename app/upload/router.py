import logging
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

logger = logging.getLogger(__name__)

router = APIRouter()

ALLOWED_EXTENSIONS = {".mp4", ".avi", ".mov", ".webm"}

# 500MB
MAX_SIZE = 500 * 1024 * 1024

# Padrão ancorado na raiz do repositório: o worker Celery procura o original
# pelo caminho absoluto devolvido aqui, então os dois processos precisam
# concordar sobre a pasta mesmo com cwd diferentes.
RAIZ_PROJETO = Path(__file__).resolve().parents[2]
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", RAIZ_PROJETO / "uploads"))


def parse_tags(tags: str) -> list[str]:
    """
    Recebe uma string separada por vírgula e transforma em lista.

    Exemplo:
    "educação, matemática" vira ["educação", "matemática"]
    """
    if not tags:
        return []

    return [tag.strip() for tag in tags.split(",") if tag.strip()]


from app.database.videos import inserir_video

def save_metadata(metadata: dict):
    """Persiste os metadados do vídeo (Supabase, ou banco local em dev).

    Antes esta função falava direto com o client do Supabase e estourava
    RuntimeError sem credenciais — o upload inteiro virava 500. Agora usa
    `inserir_video`, que tem fallback local automático.
    """
    return inserir_video(metadata)


def _enfileirar_processamento(video_id: str, file_path: str) -> str:
    """Marca status 'pending' no Redis e dispara a task do Celery.

    Devolve uma string para a resposta da API:
      * "enfileirado"  — a task foi publicada no broker;
      * "indisponivel" — Redis/broker fora do ar. O upload CONTINUA válido
        (arquivo e metadados foram salvos); só o processamento automático
        não dispara. Nada de derrubar a rota por causa da fila.

    A task `processar_upload` (app/queue/tasks.py) marca "processing", chama
    a transcodificação do João e grava "completed"/"failed" no status e no
    banco — é o que faz /status/{video_id} e o catálogo do player funcionarem.
    """
    resultado = "indisponivel"
    try:
        from app.queue.status_store import set_status
        set_status(video_id, "pending")
    except Exception:
        logger.warning("Redis fora do ar — status 'pending' não registrado para %s", video_id)

    try:
        from app.queue.tasks import processar_upload
        processar_upload.delay(video_id, file_path)
        resultado = "enfileirado"
    except Exception as exc:
        logger.warning(
            "Não foi possível enfileirar %s (broker fora do ar?): %s", video_id, exc
        )
    return resultado


@router.post("/upload")
async def upload_video(
    file: UploadFile = File(...),
    titulo: str = Form(...),
    autor: str = Form(...),
    descricao: str = Form(""),
    tags: str = Form(""),
    categoria: str = Form(""),
):
    filename = file.filename or ""
    extension = Path(filename).suffix.lower()

    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="Formato inválido. Use mp4, avi, mov ou webm.",
        )

    # Verifica tamanho do arquivo
    file.file.seek(0, os.SEEK_END)
    size = file.file.tell()
    file.file.seek(0)

    if size > MAX_SIZE:
        raise HTTPException(
            status_code=400,
            detail="Arquivo excede o tamanho máximo de 500MB.",
        )

    if not titulo.strip():
        raise HTTPException(
            status_code=400,
            detail="O título é obrigatório.",
        )

    if not autor.strip():
        raise HTTPException(
            status_code=400,
            detail="O autor é obrigatório.",
        )

    video_id = str(uuid.uuid4())

    video_folder = UPLOAD_DIR / video_id
    video_folder.mkdir(parents=True, exist_ok=True)

    file_path = video_folder / f"original{extension}"

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    metadata = {
        "video_id": video_id,
        "titulo": titulo.strip(),
        "descricao": descricao.strip(),
        "tags": parse_tags(tags),
        "categoria": categoria.strip(),
        "autor": autor.strip(),
        "criado_em": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
    }

    # Banco indisponível (tabela 'videos' não criada, credencial errada, RLS
    # bloqueando) não pode virar 500 com stacktrace: o arquivo já está no
    # disco, então avisamos com 503 legível dizendo exatamente o que conferir.
    try:
        save_metadata(metadata)
    except Exception as exc:
        logger.error("Falha ao gravar metadados no banco: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=(
                "Upload recebido, mas não foi possível gravar os metadados no "
                "banco. Rode supabase/schema.sql no SQL Editor do Supabase e "
                "confira SUPABASE_URL/SUPABASE_KEY no .env "
                "(python scripts/verificar_supabase.py diagnostica)."
            ),
        ) from exc

    fila = _enfileirar_processamento(video_id, str(file_path))

    return {
        "video_id": video_id,
        "status": "pending",
        "message": "Upload recebido com sucesso",
        "file_path": str(file_path),
        "fila": fila,
        "status_url": f"/status/{video_id}",
    }