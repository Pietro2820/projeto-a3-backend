"""Smoke test do armazenamento S3 / MinIO / Supabase Storage.

Uso (na raiz do repositório, com o venv ativado):
    python scripts/teste_storage.py

Ou dentro do container da API (não precisa de venv):
    docker compose exec api python scripts/teste_storage.py

Lê o .env da raiz (se existir), sobe um arquivo de teste, confirma que ele
existe no bucket e imprime a URL pública. Sai com código 0 se tudo passar.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))  # permite rodar de qualquer cwd


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

    try:
        storage = VideoStorage.from_env()
    except StorageError as exc:
        print(f"[ERRO] Não foi possível criar o storage: {exc}")
        return 1

    chave = "uploads/smoke/smoke.txt"
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as tmp:
        tmp.write("ok supabase")
        caminho = tmp.name

    try:
        storage.upload_arquivo(caminho, chave)
        print(f"[OK] Upload feito no bucket '{bucket}' com a chave '{chave}'.")
    except Exception as exc:
        print(f"[ERRO] Upload falhou: {exc}")
        print("Dicas: o bucket 'streaming' foi criado e está público? "
              "As chaves S3 são as novas? A região é a da página S3? "
              "O endpoint termina em /storage/v1/s3?")
        return 1
    finally:
        os.remove(caminho)

    existe = storage.existe(chave)
    print(f"[{'OK' if existe else 'ERRO'}] existe() -> {existe}")

    url = storage.url_publica(chave)
    print(f"[INFO] URL pública: {url}")
    if not os.getenv("S3_PUBLIC_URL"):
        print("[AVISO] S3_PUBLIC_URL não configurado — a URL acima usa o "
              "endpoint S3 (autenticado) e NÃO vai abrir no player!")
    else:
        print("[INFO] Abra essa URL no navegador: deve aparecer 'ok supabase'.")
    return 0 if existe else 1


if __name__ == "__main__":
    raise SystemExit(main())
