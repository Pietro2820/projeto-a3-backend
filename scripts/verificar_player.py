"""
Diagnóstico rápido do fluxo player ↔ API ↔ mídia.

Quando alguém diz "o vídeo não toca", este script responde em segundos O QUE
está faltando, sem abrir o navegador:

  1. a API responde?                       (GET /)
  2. existem vídeos concluídos no banco?   (GET /catalogo)
  3. quantos têm master.m3u8 pronto?       (campo media_pronta — disco OU bucket)
  4. a playlist é servida com o tipo certo?(GET no hls_url do catálogo: API ou
                                            bucket público, conforme a origem)
  5. as páginas do player estão no ar?     (GET /player/home.html, /player/)

Uso:
    python scripts/verificar_player.py
    python scripts/verificar_player.py --api http://localhost:8000
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

OK = "\033[32m✔\033[0m"
ERRO = "\033[31m✘\033[0m"
AVISO = "\033[33m!\033[0m"


def get_url(url: str, timeout: int = 10):
    """(status, corpo, tipo) para uma URL COMPLETA (API ou bucket público)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resposta:
            texto = resposta.read().decode("utf-8", "replace")
            tipo = resposta.headers.get("content-type", "")
            try:
                return resposta.status, json.loads(texto), tipo
            except json.JSONDecodeError:
                return resposta.status, texto, tipo
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")[:200], ""
    except Exception as exc:  # conexão recusada, DNS, timeout
        return None, str(exc), ""


def get(api: str, caminho: str, timeout: int = 10):
    """(status, corpo, tipo) de uma rota da API."""
    return get_url(f"{api}{caminho}", timeout)


def linha(simbolo: str, texto: str) -> None:
    print(f"  {simbolo} {texto}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://localhost:8000", help="origem da API")
    args = parser.parse_args()
    api = args.api.rstrip("/")

    problemas = 0

    print(f"\nAPI: {api}\n")

    # 1 ------------------------------------------------------------------
    status, corpo, _ = get(api, "/")
    if status != 200:
        linha(ERRO, f"A API não respondeu ({corpo}). Suba com: uvicorn app.main:app --reload")
        print("\nNada mais para verificar sem a API no ar.\n")
        return 1
    linha(OK, "API no ar")

    # 2 ------------------------------------------------------------------
    status, catalogo, _ = get(api, "/catalogo")
    if status != 200:
        linha(ERRO, f"GET /catalogo devolveu HTTP {status}: {str(corpo)[:120]}")
        problemas += 1
        catalogo = {"videos": []}
    else:
        videos = catalogo.get("videos", [])
        linha(OK, f"GET /catalogo: {len(videos)} vídeo(s) com status 'completed'")
        if not videos:
            linha(AVISO, "Nenhum vídeo concluído no banco — a home fica vazia de propósito.")
            linha(AVISO, "Publique um: python scripts/demo_completo.py (ou botão 'Enviar vídeo')")
            problemas += 1

    # 3 ------------------------------------------------------------------
    prontos = [v for v in catalogo.get("videos", []) if v.get("media_pronta")]
    sem_midia = [v for v in catalogo.get("videos", []) if not v.get("media_pronta")]
    if prontos:
        linha(OK, f"{len(prontos)} vídeo(s) com master.m3u8 pronto "
                  f"(disco ou bucket — tocam no player)")
    if sem_midia:
        linha(AVISO, f"{len(sem_midia)} vídeo(s) 'completed' SEM mídia: "
                     + ", ".join(v.get("video_id", "?") for v in sem_midia))
        linha(AVISO, "O player mostra esses vídeos como indisponíveis (é o esperado).")
        problemas += 1

    # 4 ------------------------------------------------------------------
    # Sonda o hls_url QUE O CATÁLOGO DEVOLVE: caminho da API quando a mídia
    # está no disco, URL pública do bucket (Supabase Storage/S3) quando está
    # lá. Sondar sempre o caminho local dava 404 enganoso em vídeo de bucket.
    for video in prontos[:2]:
        video_id = video.get("video_id")
        origem = video.get("origem_midia") or "local"
        hls = video.get("hls_url") or f"/videos/{video_id}/master.m3u8"
        url = hls if hls.startswith("http") else f"{api}{hls}"
        rotulo = url[len(api):] if url.startswith(api) else url
        status, corpo, tipo = get_url(url)
        if status == 200 and "#EXTM3U" in str(corpo):
            linha(OK, f"{rotulo} → 200 ({tipo.split(';')[0]}) [origem: {origem}]")
        else:
            linha(ERRO, f"{rotulo} → HTTP {status} ({tipo or 'sem tipo'}) "
                        f"[origem: {origem}]")
            problemas += 1

        status, corpo, _ = get(api, f"/media/{video_id}")
        if status == 200:
            linha(OK, f"/media/{video_id} → origem={corpo.get('origem')}, "
                      f"{corpo.get('segmentos')} segmento(s) no disco, "
                      f"thumbnail={corpo.get('thumbnail')}")

    # 5 ------------------------------------------------------------------
    for caminho, rotulo in (("/player/home.html", "home"), ("/player/", "watch page")):
        status, corpo, _ = get(api, caminho)
        if status == 200 and "<html" in str(corpo).lower():
            linha(OK, f"{rotulo}: {api}{caminho}")
        else:
            linha(ERRO, f"{rotulo} não está sendo servida em {caminho} (HTTP {status})")
            linha(AVISO, "A pasta PLAYER_DIR existe? (player-adaptativo/player-adaptativo)")
            problemas += 1

    print()
    if problemas:
        print(f"{AVISO} {problemas} ponto(s) de atenção acima — o player explica cada um deles na tela.\n")
        return 2
    print(f"{OK} Tudo certo: abra {api}/player/home.html e clique num vídeo.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
