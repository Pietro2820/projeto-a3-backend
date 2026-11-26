import logging
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.auth import papeis

logger = logging.getLogger(__name__)

router = APIRouter()

ALLOWED_EXTENSIONS = {".mp4", ".avi", ".mov", ".webm"}


def _max_upload_mb() -> int:
    try:
        return max(1, int(os.getenv("MAX_UPLOAD_MB", "2048")))
    except ValueError:
        return 2048


# Tamanho máximo do upload: 2 GB por padrão, configurável no .env
# (MAX_UPLOAD_MB=500, por exemplo).
MAX_SIZE = _max_upload_mb() * 1024 * 1024

# Quantos bytes do começo do arquivo bastam para reconhecer o formato.
BYTES_ASSINATURA = 16


def assinatura_confere(extension: str, cabecalho: bytes) -> bool:
    """Confere o MAGIC NUMBER do arquivo (os primeiros bytes), não só o nome.

    Extensão é só texto: um .exe renomeado para .mp4 passava direto e ia
    parar no disco e na fila do FFmpeg. Aqui o conteúdo precisa bater com o
    formato declarado:

      .mp4 / .mov  -> caixa ISO-BMFF: bytes 4..7 = "ftyp" (ou "moov"/"mdat"/
                      "wide"/"free" em .mov antigos do QuickTime)
      .avi         -> "RIFF" .... "AVI "
      .webm        -> cabeçalho EBML 1A 45 DF A3
    """
    if extension in (".mp4", ".mov"):
        return cabecalho[4:8] in (b"ftyp", b"moov", b"mdat", b"wide", b"free")
    if extension == ".avi":
        return cabecalho[:4] == b"RIFF" and cabecalho[8:12] == b"AVI "
    if extension == ".webm":
        return cabecalho[:4] == b"\x1a\x45\xdf\xa3"
    return False

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
    registro: dict = Depends(papeis.professor_ou_admin),
    file: UploadFile = File(...),
    titulo: str = Form(...),
    autor: str = Form(...),
    descricao: str = Form(""),
    tags: str = Form(""),
    categoria: str = Form(""),
):
    """Sobe um vídeo — SOMENTE professor ou admin (decisão do usuário).

    O DONO do vídeo é o usuário da SESSÃO (registro['usuario_id']) — nunca
    o campo `autor` do formulário, que é só o nome para EXIBIÇÃO no
    catálogo. Quem sobe é que pode editar e apagar (app/videos/router.py).
    """
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
            detail=f"Arquivo excede o tamanho máximo de {MAX_SIZE // (1024 * 1024)} MB.",
        )

    cabecalho = file.file.read(BYTES_ASSINATURA)
    file.file.seek(0)
    if not assinatura_confere(extension, cabecalho):
        logger.warning("Upload recusado: conteúdo não bate com %s", extension)
        raise HTTPException(
            status_code=400,
            detail=(
                "O conteúdo do arquivo não é um vídeo "
                f"{extension.lstrip('.')} válido."
            ),
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
        "professor_id": registro["usuario_id"],  # dono: sessão, não formulário
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