#!/usr/bin/env python3
"""
Gera um vídeo curto de teste com o próprio FFmpeg (Passo 1 do guia do João).

Você NÃO precisa esperar o vídeo real do Rafael para desenvolver: o FFmpeg
cria um vídeo sintético (padrão testsrc2 com contador de frames + áudio
senoidal) que serve perfeitamente para testar a transcodificação.

Uso:
    python scripts/gerar_video_teste.py
        -> cria uploads/dev/original.mp4 (30s, 1280x720, com áudio)

    python scripts/gerar_video_teste.py -o /tmp/teste.mp4 -d 10
    python scripts/gerar_video_teste.py --largura 1920 --altura 1080 -d 60
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def gerar(destino: str, duracao: int, largura: int, altura: int, fps: int = 30) -> Path:
    if shutil.which("ffmpeg") is None:
        sys.exit(
            "ERRO: FFmpeg não encontrado no PATH.\n"
            "Instale em https://ffmpeg.org/download.html\n"
            "  Windows:  winget install Gyan.FFmpeg   (ou choco install ffmpeg)\n"
            "  macOS:    brew install ffmpeg\n"
            "  Linux:    sudo apt install ffmpeg"
        )

    destino_path = Path(destino)
    destino_path.parent.mkdir(parents=True, exist_ok=True)

    comando = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-stats",
        "-f", "lavfi", "-i", f"testsrc2=size={largura}x{altura}:rate={fps}:duration={duracao}",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:duration={duracao}",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-shortest",
        str(destino_path),
    ]
    subprocess.run(comando, check=True)

    tamanho_mb = destino_path.stat().st_size / (1024 * 1024)
    print(f"✔ Vídeo de teste criado: {destino_path}")
    print(f"  {duracao}s | {largura}x{altura} | {fps}fps | ~{tamanho_mb:.1f} MB")
    return destino_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Gera vídeo de teste sintético via FFmpeg")
    parser.add_argument("-o", "--saida", default="uploads/dev/original.mp4",
                        help="caminho do arquivo de saída (padrão: uploads/dev/original.mp4)")
    parser.add_argument("-d", "--duracao", type=int, default=30,
                        help="duração em segundos (padrão: 30)")
    parser.add_argument("--largura", type=int, default=1280, help="largura (padrão: 1280)")
    parser.add_argument("--altura", type=int, default=720, help="altura (padrão: 720)")
    parser.add_argument("--fps", type=int, default=30, help="frames por segundo (padrão: 30)")
    args = parser.parse_args()

    gerar(args.saida, args.duracao, args.largura, args.altura, args.fps)


if __name__ == "__main__":
    main()
