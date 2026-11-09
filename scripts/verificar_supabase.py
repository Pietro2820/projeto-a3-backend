"""Smoke test do armazenamento S3 / MinIO / Supabase Storage.

Uso (na raiz do repositório, com o venv ativado):
    python scripts/teste_storage.py
    python scripts/teste_storage.py --debug   # log HTTP completo do botocore

Ou dentro do container da API (não precisa de venv):
    docker compose exec api python scripts/teste_storage.py

Lê o .env da raiz (se existir), investiga o bucket (head/list), sobe um
arquivo de teste, confirma que ele existe e imprime a URL pública.
Sai com código 0 se tudo passar.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))  # permite rodar de qualquer cwd

DEBUG = "--debug" in sys.argv[1:]
if DEBUG:
    import logging

    logging.basicConfig(stream=sys.stderr)
    logging.getLogger("botocore").setLevel(logging.DEBUG)


def carregar_dotenv() -> None:
    """Injeta o .env da raiz no os.environ (variáveis já definidas vencem)."""
    dotenv = REPO_ROOT / ".env"
    if not dotenv.is_file():
        return
    for linha in dotenv.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, _, valor = linha.partition("=")
        os.environ.setdefault(chave.strip(), valor.strip().strip('"').strip("'"))


def descrever_erro(exc: Exception) -> int | None:
    """Extrai status HTTP + dicas de um erro do boto3.

    O Supabase responde erros do endpoint S3 em JSON (não no XML da AWS),
    então Code/Message vêm vazios ('An error occurred ()'); o status HTTP
    é a pista que sobra.
    """
    resp = getattr(exc, "response", None) or {}
    status = (resp.get("ResponseMetadata") or {}).get("HTTPStatusCode")
    err = resp.get("Error") or {}
    print(f"[ERRO] HTTP {status} | code={err.get('Code')!r} | "
          f"msg={err.get('Message')!r}")
    if status == 404:
        print("  -> Provável: o bucket NÃO existe. Crie em "
              "Storage -> Buckets (nome 'streaming', Public bucket ON).")
    elif status == 403:
        print("  -> Provável: credenciais ou região erradas. Gere chaves NOVAS "
              "em Storage -> S3 e use exatamente a região mostrada nessa página "
              "no AWS_REGION.")
    return status


def main() -> int:
    carregar_dotenv()

    from app.transcoding.errors import StorageError
    from app.transcoding.storage import VideoStorage

    bucket = os.getenv("S3_BUCKET")
    if not bucket:
        print("[ERRO] S3_BUCKET não configurado — confira o .env "
              "(veja docs/storage-supabase.md).")
        return 1
    if os.getenv("ENABLE_S3", "").strip().lower() not in {"1", "true", "yes", "sim"}:
        print("[AVISO] ENABLE_S3 não está 'true' — o worker NÃO vai subir "
              "vídeos após transcodificar.")

    endpoint = os.getenv("AWS_ENDPOINT_URL") or "(AWS real)"
    print(f"[INFO] bucket={bucket!r} regiao={os.getenv('AWS_REGION')!r} "
          f"endpoint={endpoint}")
    if endpoint != "(AWS real)" and not os.getenv("S3_PUBLIC_URL"):
        print("[AVISO] S3_PUBLIC_URL não configurado — mesmo que o upload "
              "funcione, a URL gerada não abre no player.")

    try:
        storage = VideoStorage.from_env()
    except StorageError as exc:
        print(f"[ERRO] Não foi possível criar o storage: {exc}")
        return 1

    # -- sondagens baratas antes do upload ---------------------------------
    cliente = storage._client  # interno de propósito: sondagem de diagnóstico
    try:
        cliente.head_bucket(Bucket=bucket)
        print("[OK] head_bucket: o bucket existe e as credenciais respondem.")
    except Exception as exc:
        print("[ERRO] head_bucket falhou:")
        descrever_erro(exc)
        print("Rode com --debug para ver o corpo cru da resposta:")
        print("    python scripts/teste_storage.py --debug")
        return 1

    # -- upload de teste -----------------------------------------------------
    chave = "uploads/smoke/smoke.txt"
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as tmp:
        tmp.write("ok supabase")
        caminho = tmp.name

    try:
        storage.upload_arquivo(caminho, chave)
        print(f"[OK] Upload feito no bucket '{bucket}' com a chave '{chave}'.")
    except Exception as exc:
        print("[ERRO] Upload falhou:")
        descrever_erro(exc)
        print("Rode com --debug para ver o corpo cru da resposta:")
        print("    python scripts/teste_storage.py --debug")
        return 1
    finally:
        os.remove(caminho)

    existe = storage.existe(chave)
    print(f"[{'OK' if existe else 'ERRO'}] existe() -> {existe}")

    url = storage.url_publica(chave)
    print(f"[INFO] URL pública: {url}")
    if os.getenv("S3_PUBLIC_URL"):
        print("[INFO] Abra essa URL no navegador: deve aparecer 'ok supabase'.")
    return 0 if existe else 1


if __name__ == "__main__":
    raise SystemExit(main())
