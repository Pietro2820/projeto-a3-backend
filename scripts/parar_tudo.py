#!/usr/bin/env python3
"""
Derruba o worker Celery e a API iniciados pelo scripts/subir_tudo.py.

Usa os PID files em logs/*.pid; se o processo não responder ao término
normal, força (SIGKILL / taskkill /F). O Redis é deixado no ar de propósito
(parar o container é decisão sua: docker compose stop redis).

Uso:
    python scripts/parar_tudo.py
"""
from __future__ import annotations

import signal
import subprocess
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
LOGS = RAIZ / "logs"
PID_WORKER = LOGS / "worker.pid"
PID_API = LOGS / "api.log.pid"
EH_WINDOWS = sys.platform.startswith("win")


def processo_vivo(pid: int) -> bool:
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


def matar(pid: int, nome: str) -> None:
    if not processo_vivo(pid):
        print(f"· {nome} (pid {pid}) já não estava rodando")
        return
    if EH_WINDOWS:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        import os
        os.kill(pid, signal.SIGTERM)
        for _ in range(10):
            if not processo_vivo(pid):
                break
            time.sleep(0.5)
        if processo_vivo(pid):
            os.kill(pid, signal.SIGKILL)
    print(f"✓ {nome} (pid {pid}) encerrado")


def main() -> int:
    print("== EduStream — derrubando a stack ==")
    for arquivo, nome in ((PID_API, "API FastAPI"), (PID_WORKER, "worker Celery")):
        try:
            pid = int(arquivo.read_text().strip())
        except (OSError, ValueError):
            print(f"· sem PID file para {nome} (logs/{arquivo.name})")
            arquivo.unlink(missing_ok=True)
            continue
        matar(pid, nome)
        arquivo.unlink(missing_ok=True)

    print(
        "\nRedis continua no ar (é o broker). Para pará-lo:\n"
        "    docker compose stop redis"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
