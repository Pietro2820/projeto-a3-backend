#!/usr/bin/env python3
"""
Demo de ponta a ponta: a "conversa" completa entre backend e frontend.

O que este script faz (tudo via HTTP, como o navegador faria):
    1. Gera vídeos de teste com FFmpeg (sintéticos, poucos segundos).
    2. POST /upload             — envia cada vídeo com título, autor e tags.
    3. GET  /status/{video_id}  — acompanha pending → processing → completed
                                  (o worker Celery transcodifica em HLS).
    4. POST /watch              — registra visualizações (alimenta o trending).
    5. GET  /trending           — confere que os vídeos apareceram no ranking.
    6. GET  /videos/{id}/relacionados — confere a recomendação por tags.
    7. Imprime a URL do player:  http://localhost:8000/player/

Pré-requisitos (veja o README):
    * API no ar:        uvicorn app.main:app --reload
    * Redis no ar:      docker compose up -d redis   (ou redis-server)
    * Worker no ar:     celery -A app.queue.celery_app worker --loglevel=info --pool=solo
    * FFmpeg no PATH.

Uso:
    python scripts/demo_completo.py
    python scripts/demo_completo.py --base-url http://localhost:8000 --duracao 6
    python scripts/demo_completo.py --quantidade 1
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import time
from pathlib import Path

try:
    import httpx
except ImportError:  # pragma: no cover
    sys.exit("httpx não instalado. Rode: pip install -r requirements.txt")

# Catálogo da demo: tags pensadas para gerar relacionados interessantes
# (matemática cruza com frações/geometria; história cruza com brasil).
PRESETS = [
    {
        "titulo": "Frações — introdução",
        "autor": "Rafael",
        "descricao": "Aula demo sobre frações (gerada por scripts/demo_completo.py).",
        "tags": "matemática, frações, educação",
        "categoria": "Matemática",
    },
    {
        "titulo": "Geometria básica",
        "autor": "Gustavo",
        "descricao": "Aula demo sobre geometria (gerada por scripts/demo_completo.py).",
        "tags": "matemática, geometria, educação",
        "categoria": "Matemática",
    },
    {
        "titulo": "História do Brasil — colônia",
        "autor": "Pedro",
        "descricao": "Aula demo de história (gerada por scripts/demo_completo.py).",
        "tags": "história, brasil, educação",
        "categoria": "História",
    },
]


def gerar_video_teste(destino: Path, duracao: int) -> Path:
    """Vídeo sintético via FFmpeg (testsrc2 + seno), suficiente para HLS."""
    comando = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=25:duration={duracao}",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:duration={duracao}",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k", "-shortest",
        str(destino),
    ]
    subprocess.run(comando, check=True)
    return destino


def fazer_upload(cliente: httpx.Client, preset: dict, arquivo: Path) -> dict:
    with arquivo.open("rb") as fh:
        resposta = cliente.post(
            "/upload",
            files={"file": (f"{arquivo.stem}.mp4", fh, "video/mp4")},
            data=preset,
            timeout=60,
        )
    resposta.raise_for_status()
    return resposta.json()


def aguardar_processamento(cliente: httpx.Client, video_id: str,
                           timeout: int = 600) -> dict:
    """Faz polling de /status até completed/failed (o worker transcodifica)."""
    inicio = time.time()
    ultimo = None
    while time.time() - inicio < timeout:
        resposta = cliente.get(f"/status/{video_id}", timeout=10)
        if resposta.status_code == 200:
            status = resposta.json()
            if status.get("status") != ultimo:
                ultimo = status.get("status")
                print(f"    status: {ultimo}")
            if ultimo in {"completed", "failed"}:
                return status
        time.sleep(2)
    raise TimeoutError(f"vídeo {video_id} não terminou em {timeout}s")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--duracao", type=int, default=6,
                        help="duração (s) de cada vídeo de teste (padrão: 6)")
    parser.add_argument("--quantidade", type=int, default=len(PRESETS),
                        help=f"quantos vídeos enviar (1 a {len(PRESETS)})")
    args = parser.parse_args()

    quantidade = max(1, min(args.quantidade, len(PRESETS)))
    cliente = httpx.Client(base_url=args.base_url)

    print(f"== Demo back ↔ front em {args.base_url} ==")
    try:
        saude = cliente.get("/", timeout=5).json()
        print(f"API respondeu: {saude.get('status')}")
    except Exception as exc:
        sys.exit(
            f"API fora do ar em {args.base_url}: {exc}\n"
            "Suba primeiro: uvicorn app.main:app  (veja o README)"
        )

    enviados = []
    with tempfile.TemporaryDirectory() as tmp:
        for indice, preset in enumerate(PRESETS[:quantidade]):
            print(f"\n[1/3] Gerando e enviando: {preset['titulo']}")
            arquivo = gerar_video_teste(Path(tmp) / f"demo_{indice}.mp4", args.duracao)
            corpo = fazer_upload(cliente, preset, arquivo)
            print(f"    video_id: {corpo['video_id']}  fila: {corpo.get('fila')}")
            enviados.append((preset, corpo))

        for preset, corpo in enviados:
            print(f"\n[2/3] Acompanhando processamento: {preset['titulo']}")
            status = aguardar_processamento(cliente, corpo["video_id"])
            if status.get("status") != "completed":
                print(f"    FALHOU: {status}")
                return 1

        print("\n[3/3] Registrando visualizações e consultando a API")
        for peso, (preset, corpo) in enumerate(enviados, start=3):
            for _ in range(peso):  # pesos diferentes para o ranking fazer sentido
                cliente.post("/watch", json={
                    "video_id": corpo["video_id"], "user_id": "demo-user",
                }, timeout=10)

    trending = cliente.get("/trending", timeout=10).json().get("trending", [])
    print("\n/trending:")
    for item in trending:
        print(f"    {item['views']:>3} views — {item['titulo']}  ({item['hls_url']})")

    primeiro_id = enviados[0][1]["video_id"]
    relacionados = cliente.get(
        f"/videos/{primeiro_id}/relacionados", timeout=10
    ).json().get("relacionados", [])
    print(f"\n/videos/{primeiro_id[:8]}.../relacionados:")
    for item in relacionados:
        print(f"    score {item['score']} — {item['titulo']}")

    print("\n== Tudo certo! Abra o player: ==")
    print(f"    Home (grade de vídeos):  {args.base_url}/player/home.html")
    print(f"    Watch (player):          {args.base_url}/player/")
    print("O player vai carregar o catálogo real (em alta + relacionados) e")
    print("tocar o primeiro vídeo transcodificado automaticamente.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
