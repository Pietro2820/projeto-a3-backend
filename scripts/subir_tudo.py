#!/usr/bin/env python3
"""
Sobe a stack inteira com UM comando (Redis + worker Celery + API).

Substitui os "4 terminais" do README para o dia a dia e para a apresentação:

    python scripts/subir_tudo.py            # tudo em background, logs em logs/
    python scripts/subir_tudo.py --demo     # ...e roda a demo E2E em seguida
    python scripts/subir_tudo.py --frente   # tudo neste terminal (Ctrl+C derruba)
    python scripts/parar_tudo.py            # derruba worker + API

O que ele faz, em ordem:
    1. cria .env a partir do .env.example se não existir (compose exige);
    2. Redis: se a porta 6379 não responder, tenta `docker compose up -d redis`
       (sem Docker/Redis a stack sobe, mas sem fila: upload não transcodifica);
    3. worker Celery: `python -m celery -A app.queue.celery_app worker --pool=solo`
       (--pool=solo é o que funciona no Windows, fora do Docker);
    4. API: `python -m uvicorn app.main:app --port 8000`;
    5. health check em GET / e imprime as URLs do player/home/docs.

Os PIDs ficam em logs/*.pid — é assim que o parar_tudo.py sabe o que matar.
Rode com o python do venv ativado (venv\\Scripts\\python.exe no Windows).
"""
from __future__ import annotations

import argparse
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
LOGS = RAIZ / "logs"
PID_WORKER = LOGS / "worker.pid"
PID_API = LOGS / "api.log.pid"
PORTA_API = 8000
PORTA_REDIS = 6379
EH_WINDOWS = sys.platform.startswith("win")


def log(msg: str) -> None:
    print(msg, flush=True)


def porta_aberta(porta: int, host: str = "127.0.0.1") -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex((host, porta)) == 0


def processo_vivo(pid: int | None) -> bool:
    if not pid:
        return False
    if EH_WINDOWS:
        saida = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True,
        ).stdout
        return str(pid) in saida
    try:
        import os
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def ler_pid(arquivo: Path) -> int | None:
    try:
        return int(arquivo.read_text().strip())
    except (OSError, ValueError):
        return None


def matar(pid: int | None) -> bool:
    if not processo_vivo(pid):
        return False
    if EH_WINDOWS:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
        )
    else:
        import os
        os.kill(pid, signal.SIGTERM)
        for _ in range(10):
            if not processo_vivo(pid):
                break
            time.sleep(0.5)
        if processo_vivo(pid):
            os.kill(pid, signal.SIGKILL)
    return True


def parar_instancias_antigas() -> None:
    """Evita 'address already in use' de uma execução anterior nossa."""
    for arquivo in (PID_WORKER, PID_API):
        pid = ler_pid(arquivo)
        if matar(pid):
            log(f"· parando instância anterior (pid {pid})")
        arquivo.unlink(missing_ok=True)
    time.sleep(1)


def garantir_env() -> None:
    env = RAIZ / ".env"
    if not env.exists():
        shutil.copyfile(RAIZ / ".env.example", env)
        log("· .env criado a partir do .env.example (vazio mesmo, ok)")


def garantir_redis() -> bool:
    if porta_aberta(PORTA_REDIS):
        log(f"✓ Redis já no ar na porta {PORTA_REDIS}")
        return True

    docker = shutil.which("docker")
    compose_file = RAIZ / "docker-compose.yml"
    if docker and compose_file.exists():
        log("· subindo Redis via docker compose (pode pedir o Docker Desktop aberto)...")
        subprocess.run(
            [docker, "compose", "up", "-d", "redis"],
            cwd=RAIZ, capture_output=True, text=True,
        )
        for _ in range(30):
            if porta_aberta(PORTA_REDIS):
                log("✓ Redis subiu via Docker")
                return True
            time.sleep(1)

    log(
        "⚠ Redis indisponível: a API sobe, mas SEM fila — uploads não serão\n"
        "  transcodificados e o /trending fica vazio. Para ter tudo:\n"
        "  abra o Docker Desktop e rode: docker compose up -d redis"
    )
    return False


def iniciar_processo(nome: str, args: list[str], log_name: str, pid_file: Path):
    LOGS.mkdir(exist_ok=True)
    log_handle = open(LOGS / log_name, "ab", buffering=0)
    processo = subprocess.Popen(
        args,
        cwd=RAIZ,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )
    pid_file.write_text(str(processo.pid))
    log(f"✓ {nome} iniciado (pid {processo.pid}, log em logs/{log_name})")
    return processo


def aguardar_api(timeout: int = 30) -> bool:
    url = f"http://127.0.0.1:{PORTA_API}/"
    inicio = time.time()
    while time.time() - inicio < timeout:
        try:
            with urllib.request.urlopen(url, timeout=2) as resposta:
                if resposta.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(1)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true",
                        help="roda scripts/demo_completo.py após subir tudo")
    parser.add_argument("--frente", action="store_true",
                        help="mantém worker+API neste terminal (Ctrl+C derruba)")
    args = parser.parse_args()

    log("== EduStream — subindo a stack ==")
    parar_instancias_antigas()

    if porta_aberta(PORTA_API):
        log(
            f"✗ A porta {PORTA_API} já está em uso por OUTRO processo\n"
            "  (um uvicorn aberto em outro terminal?). Feche-o ou rode\n"
            "  python scripts/parar_tudo.py e tente de novo."
        )
        return 1

    garantir_env()
    garantir_redis()

    worker = iniciar_processo(
        "worker Celery",
        [sys.executable, "-m", "celery", "-A", "app.queue.celery_app",
         "worker", "--loglevel=info", "--pool=solo"],
        "worker.log", PID_WORKER,
    )
    api = iniciar_processo(
        "API FastAPI",
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "0.0.0.0", "--port", str(PORTA_API)],
        "api.log", PID_API,
    )

    log("· aguardando a API responder...")
    if not aguardar_api():
        log(
            "✗ A API não respondeu em 30s. Veja o final de logs/api.log\n"
            "  (porta ocupada? dependências faltando no venv?)"
        )
        return 1

    log("✓ API no ar!")
    log(f"    Home do player:  http://localhost:{PORTA_API}/player/home.html")
    log(f"    Watch (player):  http://localhost:{PORTA_API}/player/")
    log(f"    Docs da API:     http://localhost:{PORTA_API}/docs")

    if args.demo:
        log("\n== rodando a demo E2E (upload → transcode → catálogo) ==")
        subprocess.run([sys.executable, str(RAIZ / "scripts" / "demo_completo.py")], cwd=RAIZ)

    if args.frente:
        log("\nCtrl+C para derrubar tudo.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            log("\n· encerrando...")
            matar(worker.pid)
            matar(api.pid)
            PID_WORKER.unlink(missing_ok=True)
            PID_API.unlink(missing_ok=True)
    else:
        log("\nTudo em background. Para derrubar: python scripts/parar_tudo.py")

    return 0


if __name__ == "__main__":
    sys.exit(main())
