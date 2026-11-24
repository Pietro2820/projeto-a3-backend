#!/usr/bin/env python3
"""
Sobe a stack inteira com UM comando (Redis + worker Celery + API).

Substitui os "4 terminais" do README para o dia a dia e para a apresentação:
    python scripts/subir_tudo.py            # tudo em background, logs em logs/
    python scripts/subir_tudo.py --demo     # ...e roda a demo E2E em seguida
    python scripts/subir_tudo.py --frente   # tudo neste terminal (Ctrl+C derruba)
    python scripts/parar_tudo.py            # derruba worker + API

O que ele faz, em ordem:
    1. Verifica se está na raiz do projeto.
    2. Libera forçosamente as portas 8000 e 6379 se houver processos zumbis.
    3. Cria .env a partir do .env.example se não existir.
    4. Redis: se a porta 6379 não responder, tenta `docker compose up -d redis`.
    5. Worker Celery: `python -m celery -A app.queue.celery_app worker --pool=solo`
       (--pool=solo é o que funciona no Windows, fora do Docker).
    6. API: `python -m uvicorn app.main:app --port 8000`.
    7. Health check em GET / e imprime as URLs do player/home/docs.

Os PIDs ficam em logs/*.pid — é assim que o parar_tudo.py sabe o que matar.
Rode com o python do venv ativado (venv\Scripts\python.exe no Windows).
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


def liberar_porta_forcada(porta: int) -> None:
    """Descobre e mata qualquer processo travado na porta (resolve o 'Address already in use')."""
    if not porta_aberta(porta):
        return
    
    log(f"⚠ Porta {porta} em uso. Tentando liberar automaticamente...")
    if EH_WINDOWS:
        try:
            # Pega a saída do netstat e extrai o PID (última coluna)
            resultado = subprocess.run(
                f'netstat -ano | findstr ":{porta}" | findstr "LISTENING"',
                capture_output=True, text=True, shell=True
            )
            if resultado.stdout:
                # A linha é algo como: "  TCP    0.0.0.0:8000    0.0.0.0:0    LISTENING    25796"
                pid_str = resultado.stdout.strip().split()[-1]
                if pid_str.isdigit():
                    log(f"  → Matando processo PID {pid_str} que travou a porta {porta}...")
                    subprocess.run(["taskkill", "/F", "/PID", pid_str], capture_output=True)
                    time.sleep(1) # Dá um tempo pro SO liberar a porta
        except Exception:
            pass # Se falhar, o script principal vai reclamar depois, mas tentamos.


def checar_ambiente() -> bool:
    if sys.version_info < (3, 10):
        log(
            f"✗ Python {sys.version_info.major}.{sys.version_info.minor} é velho "
            "demais — o projeto usa 'str | None' (3.10+).\n"
            "  Instale Python 3.10+ e recrie o venv:\n"
            "  python -m venv venv  →  venv\\Scripts\\python.exe -m pip install -r requirements.txt"
        )
        return False

    faltando = []
    for modulo in ("fastapi", "uvicorn", "celery", "redis", "supabase"):
        try:
            __import__(modulo)
        except ImportError:
            faltando.append(modulo)
    if faltando:
        log(
            "✗ Dependências faltando no python em uso: " + ", ".join(faltando) + "\n"
            f"  Python atual: {sys.executable}\n"
            "  Rode:  python -m pip install -r requirements.txt\n"
            "  (ou use o venv: venv\\Scripts\\python.exe scripts\\subir_tudo.py)"
        )
        return False

    log(f"✓ Python ok ({sys.version_info.major}.{sys.version_info.minor}) e dependências presentes")
    return True


def mostrar_fim_do_log(arquivo: Path, linhas: int = 30) -> None:
    if not arquivo.exists():
        log("(não chegou a escrever o log — processo nem começou)")
        return
    
    try:
        conteudo = arquivo.read_text(errors="replace").splitlines()
    except PermissionError:
        log(f"(não foi possível ler {arquivo.name} pois o processo ainda o mantém aberto no Windows)")
        return

    if not conteudo:
        log(f"({arquivo.name} vazio)")
        return
        
    log(f"---- últimas {min(linhas, len(conteudo))} linhas de {arquivo.name} ----")
    for linha in conteudo[-linhas:]:
        log(f"  {linha}")
    log("-" * 60)


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
    """Evita 'address already in use' de uma execução anterior."""
    # 1. Tenta matar pelos PIDs salvos
    for arquivo in (PID_WORKER, PID_API):
        pid = ler_pid(arquivo)
        if matar(pid):
            log(f"· parando instância anterior registrada (pid {pid})")
        arquivo.unlink(missing_ok=True)
    
    # 2. Garantia extra: varre e mata o que estiver travando as portas
    liberar_porta_forcada(PORTA_API)
    liberar_porta_forcada(PORTA_REDIS)
    
    time.sleep(1.5) # Tempo de respiro para o SO liberar os sockets


def garantir_env() -> None:
    env = RAIZ / ".env"
    if not env.exists():
        exemplo = RAIZ / ".env.example"
        if exemplo.exists():
            shutil.copyfile(exemplo, env)
            log("· .env criado a partir do .env.example")
        else:
            log("⚠ .env.example não encontrado. Crie um arquivo .env manualmente.")


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
        "  transcodificados. Para ter tudo, abra o Docker Desktop e rode:\n"
        "  docker compose up -d redis"
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


def aguardar_api(timeout: int = 45) -> bool:
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
    if not (RAIZ / "app" / "main.py").exists():
        log(f"✗ Erro: Este script deve ser rodado da raiz do projeto.\nDiretório atual: {RAIZ}")
        return 1

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
            f"✗ A porta {PORTA_API} continua em uso após tentativa de limpeza.\n"
            f"  Feche manualmente ou reinicie o terminal."
        )
        return 1

    if not checar_ambiente():
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
        log("✗ A API não respondeu em 45s. O motivo real está logo abaixo:")
        mostrar_fim_do_log(LOGS / "api.log")
        log("\n  💡 Dicas comuns de falha na inicialização:")
        log("     1. Erro de sintaxe em algum arquivo .py recém-editado.")
        log("     2. Variável de ambiente obrigatória faltando no .env.")
        log("     3. Conflito de importação (ex: nome de arquivo igual ao de um módulo).")
        return 1

    log("✅ API no ar com sucesso!")
    log(f"   🌐 Home do player:  http://localhost:{PORTA_API}/player/home.html")
    log(f"   📺 Watch (player):  http://localhost:{PORTA_API}/player/")
    log(f"   📚 Docs da API:     http://localhost:{PORTA_API}/docs")

    if args.demo:
        log("\n== rodando a demo E2E (upload → transcode → catálogo) ==")
        subprocess.run([sys.executable, str(RAIZ / "scripts" / "demo_completo.py")], cwd=RAIZ)

    if args.frente:
        log("\nPressione Ctrl+C para derrubar tudo.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            log("\n· encerrando processos...")
            matar(worker.pid)
            matar(api.pid)
            PID_WORKER.unlink(missing_ok=True)
            PID_API.unlink(missing_ok=True)
            log("✓ Tudo encerrado.")
    else:
        log("\n🔄 Tudo rodando em background. Para derrubar, execute: python scripts/parar_tudo.py")

    return 0


if __name__ == "__main__":
    sys.exit(main())