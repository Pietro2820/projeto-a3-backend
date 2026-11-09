"""
Publica o cenário de testes usado pelo teste E2E do player
(`player-adaptativo/player-adaptativo/tests/e2e.player-api.test.js`).

Três vídeos no banco (Supabase se configurado, senão data/videos.json):

  v-pronto                   concluído + HLS no disco   → TOCA
  v-completed-sem-arquivo    concluído, mas sem mídia   → player explica (sem loop de 404)
  v-sem-midia                em processamento           → não entra no catálogo

Ele NÃO precisa de Redis nem de worker Celery: escreve os metadados direto no
banco e gera os arquivos HLS em videos/{id}/ (com FFmpeg, se instalado; senão
uma playlist mínima que já basta para validar o fluxo do player).

Uso:
    python scripts/seed_e2e.py            # publica o cenário
    python scripts/seed_e2e.py --limpar   # remove os três vídeos do banco
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from app import media  # noqa: E402  (import depois do sys.path)
from app.database import local_store  # noqa: E402
from app.database.videos import buscar_video, inserir_video  # noqa: E402

VIDEOS = [
    {
        "video_id": "v-pronto",
        "titulo": "Frações — aula 1 (E2E)",
        "descricao": "Vídeo de teste com HLS gerado: é o que o player deve tocar.",
        "tags": ["matemática", "frações", "e2e"],
        "categoria": "Matemática",
        "autor": "Rafael",
        "status": "completed",
    },
    {
        "video_id": "v-completed-sem-arquivo",
        "titulo": "Concluído sem arquivo HLS (E2E)",
        "descricao": "Metadado diz completed, mas não há master.m3u8 — o player "
                      "precisa explicar em vez de ficar repetindo um 404.",
        "tags": ["teste", "e2e"],
        "categoria": "QA",
        "autor": "Pedro",
        "status": "completed",
    },
    {
        "video_id": "v-sem-midia",
        "titulo": "Ainda transcodificando (E2E)",
        "descricao": "Status processing: não entra no catálogo (prontos=1).",
        "tags": ["git", "e2e"],
        "categoria": "Dev",
        "autor": "Pietro",
        "status": "processing",
    },
]


def escrever_master(destino: Path) -> None:
    """Escreve o master.m3u8 na RAIZ do vídeo (videos/{id}/master.m3u8).

    É o layout do contrato do projeto (app/media.py e o docstring de
    app/transcoding/ffmpeg_service.py): o master fica na raiz do vídeo e
    aponta caminhos relativos "{resolucao}/playlist.m3u8". A API serve
    hls_url = /videos/{id}/master.m3u8 — em qualquer outro lugar o player
    recebe 404 e o vídeo não toca.
    """
    (destino / "master.m3u8").write_text(
        "#EXTM3U\n#EXT-X-VERSION:3\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=464000,RESOLUTION=640x360\n360p/playlist.m3u8\n",
        encoding="utf-8",
    )


def gerar_hls_com_ffmpeg(video_id: str) -> bool:
    """Gera um HLS de 6s (vídeo sintético) usando o FFmpeg do sistema.

    Sem `-master_pl_name` de propósito: o FFmpeg escreve o master AO LADO da
    playlist de saída (videos/{id}/360p/master.m3u8), mas o contrato do
    projeto é o master na RAIZ (videos/{id}/master.m3u8). Com o master no
    lugar errado, `media_pronta` dava False, o /catalogo?prontos=1 vinha vazio
    e o hls_url devolvia 404 — exatamente o sintoma "o vídeo não roda no
    player" em qualquer máquina COM FFmpeg (o README o lista como
    pré-requisito, e o CI ubuntu-latest já traz instalado). Por isso o master
    é escrito aqui, igual ao layout da transcodificação real.
    """
    if not shutil.which("ffmpeg"):
        return False

    destino = media.diretorio_do_video(video_id)
    (destino / "360p").mkdir(parents=True, exist_ok=True)
    comando = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=6",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
        "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac",
        "-hls_time", "2", "-hls_playlist_type", "vod",
        "-hls_segment_filename", str(destino / "360p" / "segment_%03d.ts"),
        str(destino / "360p" / "playlist.m3u8"),
    ]
    if subprocess.run(comando, check=False).returncode != 0:
        return False
    escrever_master(destino)
    return True


def gerar_hls_minimo(video_id: str) -> None:
    """Sem FFmpeg: playlist mínima (basta para o player achar o master.m3u8)."""
    destino = media.diretorio_do_video(video_id) / "360p"
    destino.mkdir(parents=True, exist_ok=True)
    escrever_master(destino.parent)
    (destino / "playlist.m3u8").write_text(
        "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:4\n"
        "#EXTINF:4.0,\nsegment_000.ts\n#EXT-X-ENDLIST\n",
        encoding="utf-8",
    )
    (destino / "segment_000.ts").write_bytes(b"\x47" + b"\x00" * 187)


def publicar() -> None:
    for video in VIDEOS:
        if buscar_video(video["video_id"]) is None:
            inserir_video(dict(video))
            print(f"  + metadado {video['video_id']} ({video['status']})")
        else:
            print(f"  = metadado {video['video_id']} já existia")

    if gerar_hls_com_ffmpeg("v-pronto"):
        print("  + HLS real gerado com FFmpeg para v-pronto")
    else:
        gerar_hls_minimo("v-pronto")
        print("  + HLS mínimo gerado para v-pronto (FFmpeg não está no PATH)")

    # Os outros dois ficam SEM mídia de propósito.
    for video_id in ("v-completed-sem-arquivo", "v-sem-midia"):
        pasta = media.diretorio_do_video(video_id)
        if pasta.exists():
            shutil.rmtree(pasta)

    print("\nCenário pronto. Confira em:")
    print("  GET /catalogo?prontos=1  ->  só 'v-pronto'")
    print("  GET /media/v-pronto      ->  media_pronta: true")


def limpar() -> None:
    banco = local_store.caminho_do_banco()
    ids = {video["video_id"] for video in VIDEOS}

    if banco.is_file():
        import json

        conteudo = json.loads(banco.read_text(encoding="utf-8"))
        restante = [item for item in conteudo if item.get("video_id") not in ids]
        banco.write_text(json.dumps(restante, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  - {len(conteudo) - len(restante)} metadados removidos do banco local")
    else:
        print("  (banco local não existe — nada a limpar; se usa Supabase, apague pelo painel)")

    for video_id in ids:
        pasta = media.diretorio_do_video(video_id)
        if pasta.exists():
            shutil.rmtree(pasta)
            print(f"  - mídia de {video_id} removida")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limpar", action="store_true", help="remove o cenário publicado")
    args = parser.parse_args()

    limpar() if args.limpar else publicar()
