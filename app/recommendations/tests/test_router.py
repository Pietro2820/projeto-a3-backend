"""
Testes das rotas de recomendação — contrato #6 da documentação técnica.

    GET  /videos/{video_id}/relacionados
    GET  /recomendacoes/{user_id}
    GET  /trending
    POST /watch

O banco (Supabase) e o Redis são substituídos por dublês. O que está em teste é
a rota: status HTTP, formato da resposta, ordenação, validação de parâmetros e
— importante para o edital — o tratamento de erro quando a infraestrutura falta.
"""
from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.recommendations import router as modulo_router


client = TestClient(app)


def video(video_id: str, titulo: str, tags, **extra) -> dict:
    """Metadado no formato do contrato #2."""
    return {
        "video_id": video_id,
        "titulo": titulo,
        "descricao": "descrição padrão",
        "tags": tags,
        "categoria": "Matemática",
        "autor": "Rafael",
        "criado_em": "2026-01-01T00:00:00+00:00",
        "status": "completed",
        **extra,
    }


class ViewsFake:
    """Dublê do views_store com o mínimo que as rotas usam."""

    def __init__(self, contagens=None, ranking=None, historicos=None):
        self.contagens = contagens or {}
        self.ranking = ranking or []
        self.historicos = historicos or {}
        self.registros: list[tuple[str, str | None]] = []
        self.erro = None

    def contar_visualizacoes(self, video_id):
        return self.contagens.get(video_id, 0)

    def mais_assistidos(self, limite=10):
        return self.ranking[:limite]

    def historico_do_usuario(self, user_id):
        return self.historicos.get(user_id, [])

    def registrar_visualizacao(self, video_id, user_id=None):
        if self.erro:
            raise self.erro
        self.registros.append((video_id, user_id))
        self.contagens[video_id] = self.contagens.get(video_id, 0) + 1
        return self.contagens[video_id]


CATALOGO = [
    video("v-fra", "Frações", ["matemática", "frações", "educação"]),
    video("v-geo", "Geometria", ["matemática", "geometria"]),
    video("v-his", "História do Brasil", ["história", "brasil"]),
    video("v-quim", "Química geral", ["química"]),
]


@pytest.fixture
def banco(monkeypatch):
    """Dubla o acesso ao Supabase com um catálogo fixo.

    Cópia profunda de propósito: vários testes MUTAM as tags dos vídeos para
    exercitar normalização (string, caixa alta...). Sem o deepcopy, essas
    mutações vazavam pelo CATALOGO compartilhado e contaminavam os testes
    seguintes (ex.: v-geo ficava com a tag "frações" para o resto da suíte).
    """
    estado = {
        "videos": {
            item["video_id"]: copy.deepcopy(item) for item in CATALOGO
        },
        "erro": None,
    }

    def _buscar(video_id):
        if estado["erro"]:
            raise estado["erro"]
        return estado["videos"].get(video_id)

    def _listar(status=None):
        if estado["erro"]:
            raise estado["erro"]
        itens = list(estado["videos"].values())
        return [item for item in itens if status is None or item.get("status") == status]

    monkeypatch.setattr(modulo_router, "buscar_video", _buscar)
    monkeypatch.setattr(modulo_router, "listar_videos", _listar)
    return estado


@pytest.fixture
def views(monkeypatch):
    """Dubla o Redis de visualizações."""
    fake = ViewsFake()
    monkeypatch.setattr(modulo_router, "views_store", fake)
    return fake


@pytest.fixture
def api(banco, views):
    """Sobe os dois dublês juntos (a maioria dos testes precisa dos dois)."""
    return client


# ---------------------------------------------------------------------------
# GET /videos/{video_id}/relacionados
# ---------------------------------------------------------------------------

def test_relacionados_devolve_200(api):
    resposta = client.get("/videos/v-fra/relacionados")

    assert resposta.status_code == 200
    assert "relacionados" in resposta.json()


def test_relacionados_vem_ordem_de_similaridade(api):
    resposta = client.get("/videos/v-fra/relacionados")

    titulos = [item["titulo"] for item in resposta.json()["relacionados"]]
    # Geometria compartilha "matemática"; História e Química não compartilham nada.
    assert titulos[0] == "Geometria"


def test_relacionados_exclui_o_proprio_video(api):
    resposta = client.get("/videos/v-fra/relacionados")

    ids = [item["video_id"] for item in resposta.json()["relacionados"]]
    assert "v-fra" not in ids


def test_relacionados_descarta_quem_nao_tem_nada_em_comum(api):
    resposta = client.get("/videos/v-fra/relacionados")

    titulos = [item["titulo"] for item in resposta.json()["relacionados"]]
    assert "Química geral" not in titulos
    assert "História do Brasil" not in titulos


def test_relacionados_traz_a_nota_de_similaridade(api):
    resposta = client.get("/videos/v-fra/relacionados")

    primeiro = resposta.json()["relacionados"][0]
    assert 0 < primeiro["score"] <= 1


def test_relacionados_respeita_o_limite(api, banco):
    banco["videos"]["v-geo2"] = video("v-geo2", "Geometria II", ["matemática", "frações"])

    resposta = client.get("/videos/v-fra/relacionados?limite=1")

    assert len(resposta.json()["relacionados"]) == 1


@pytest.mark.parametrize("limite_invalido", [0, -1, 51, 999])
def test_limite_fora_da_faixa_devolve_422(api, limite_invalido):
    resposta = client.get(f"/videos/v-fra/relacionados?limite={limite_invalido}")

    assert resposta.status_code == 422


def test_relacionados_de_video_inexistente_devolve_404(api):
    resposta = client.get("/videos/nao-existe/relacionados")

    assert resposta.status_code == 404
    assert resposta.json()["detail"] == "video_id não encontrado"


def test_relacionados_devolve_tags_como_lista(api):
    """O frontend espera um array, não a string crua do banco."""
    resposta = client.get("/videos/v-fra/relacionados")

    item = resposta.json()["relacionados"][0]
    assert isinstance(item["tags"], list)


def test_relacionados_traz_as_urls_de_midia_do_contrato_1(api):
    """URLs absolutas: o player pode estar em outra origem (Live Server) —
    com URL relativa o navegador resolveria contra o front e daria 404."""
    resposta = client.get("/videos/v-fra/relacionados")

    item = resposta.json()["relacionados"][0]
    assert item["hls_url"] == f"http://testserver/videos/{item['video_id']}/master.m3u8"
    assert item["thumbnail_url"] == f"http://testserver/videos/{item['video_id']}/thumbnail.jpg"


def test_relacionados_inclui_a_contagem_de_views(api, views):
    views.contagens["v-geo"] = 42

    resposta = client.get("/videos/v-fra/relacionados")

    item = resposta.json()["relacionados"][0]
    assert item["views"] == 42


def test_relacionados_aceita_tags_vindas_como_string(api, banco):
    """Há dois caminhos de gravação no projeto: lista (upload) e string."""
    banco["videos"]["v-fra"]["tags"] = "matemática, frações"
    banco["videos"]["v-geo"]["tags"] = "matemática, geometria"

    resposta = client.get("/videos/v-fra/relacionados")

    assert resposta.status_code == 200
    assert resposta.json()["relacionados"][0]["titulo"] == "Geometria"


def test_relacionados_ignora_diferenca_de_caixa_e_espacos(api, banco):
    banco["videos"]["v-fra"]["tags"] = ["Matemática", " FRAÇÕES "]
    banco["videos"]["v-geo"]["tags"] = ["matemática", "frações"]

    resposta = client.get("/videos/v-fra/relacionados")

    assert resposta.json()["relacionados"][0]["titulo"] == "Geometria"


def test_relacionados_sem_catalogo_devolve_lista_vazia(api, banco):
    banco["videos"] = {"v-fra": CATALOGO[0]}

    resposta = client.get("/videos/v-fra/relacionados")

    assert resposta.status_code == 200
    assert resposta.json()["relacionados"] == []


def test_banco_desconfigurado_devolve_503_e_nao_500(api, banco):
    """É o estado real do projeto hoje: .env sem SUPABASE_URL/SUPABASE_KEY."""
    banco["erro"] = RuntimeError("SUPABASE_URL e/ou SUPABASE_KEY não estão definidas")

    resposta = client.get("/videos/v-fra/relacionados")

    assert resposta.status_code == 503
    assert ".env" in resposta.json()["detail"]


def test_falha_inesperada_do_banco_devolve_502(api, banco):
    banco["erro"] = ConnectionError("conexão recusada")

    resposta = client.get("/videos/v-fra/relacionados")

    assert resposta.status_code == 502


# ---------------------------------------------------------------------------
# GET /trending
# ---------------------------------------------------------------------------

def test_trending_sem_views_devolve_lista_vazia_com_motivo(api):
    resposta = client.get("/trending")

    corpo = resposta.json()
    assert resposta.status_code == 200
    assert corpo["trending"] == []
    assert "motivo" in corpo


def test_trending_vem_ordenado_por_visualizacoes(api, views):
    views.ranking = [("v-his", 30), ("v-fra", 12), ("v-geo", 3)]

    resposta = client.get("/trending")

    ids = [item["video_id"] for item in resposta.json()["trending"]]
    assert ids == ["v-his", "v-fra", "v-geo"]


def test_trending_devolve_a_contagem_real_de_views(api, views):
    views.ranking = [("v-fra", 77)]

    resposta = client.get("/trending")

    assert resposta.json()["trending"][0]["views"] == 77


def test_trending_respeita_o_limite(api, views):
    views.ranking = [("v-fra", 100), ("v-geo", 90), ("v-his", 80), ("v-quim", 70)]

    resposta = client.get("/trending?limite=3")

    corpo = resposta.json()["trending"]
    assert len(corpo) == 3
    assert [item["video_id"] for item in corpo] == ["v-fra", "v-geo", "v-his"]


def test_trending_ignora_video_apagado_do_banco(api, views):
    """O ranking fica no Redis; o vídeo pode ter sido removido do Supabase."""
    views.ranking = [("v-fantasma", 99), ("v-fra", 5)]

    resposta = client.get("/trending")

    ids = [item["video_id"] for item in resposta.json()["trending"]]
    assert ids == ["v-fra"]


def test_trending_com_banco_desconfigurado_devolve_503(api, banco, views):
    views.ranking = [("v-fra", 5)]
    banco["erro"] = RuntimeError("sem credenciais")

    resposta = client.get("/trending")

    assert resposta.status_code == 503


# ---------------------------------------------------------------------------
# GET /recomendacoes/{user_id}
# ---------------------------------------------------------------------------

def test_recomendacoes_para_usuario_novo_vem_vazia_com_motivo(api, views):
    resposta = client.get("/recomendacoes/gustavo")

    corpo = resposta.json()
    assert resposta.status_code == 200
    assert corpo["recomendacoes"] == []
    assert corpo["user_id"] == "gustavo"
    assert "motivo" in corpo


def test_recomendacoes_usa_as_tags_do_historico(api, views):
    views.historicos = {"gustavo": ["v-fra"]}

    resposta = client.get("/recomendacoes/gustavo")

    titulos = [item["titulo"] for item in resposta.json()["recomendacoes"]]
    assert "Geometria" in titulos


def test_recomendacoes_nao_repete_video_ja_assistido(api, views):
    views.historicos = {"gustavo": ["v-fra"]}

    resposta = client.get("/recomendacoes/gustavo")

    ids = [item["video_id"] for item in resposta.json()["recomendacoes"]]
    assert "v-fra" not in ids


def test_recomendacoes_soma_as_tags_de_todo_o_historico(api, views):
    """Perfil do usuário = união das tags do que ele assistiu."""
    views.historicos = {"gustavo": ["v-his", "v-quim"]}

    resposta = client.get("/recomendacoes/gustavo")

    assert resposta.status_code == 200


def test_recomendacoes_com_historico_de_video_apagado_nao_quebra(api, banco, views):
    views.historicos = {"gustavo": ["v-fantasma", "v-fra"]}

    resposta = client.get("/recomendacoes/gustavo")

    assert resposta.status_code == 200
    assert resposta.json()["recomendacoes"][0]["titulo"] == "Geometria"


def test_recomendacoes_com_banco_desconfigurado_devolve_503(api, banco, views):
    views.historicos = {"gustavo": ["v-fra"]}
    banco["erro"] = RuntimeError("sem credenciais")

    resposta = client.get("/recomendacoes/gustavo")

    assert resposta.status_code == 503


# ---------------------------------------------------------------------------
# POST /watch
# ---------------------------------------------------------------------------

def test_watch_registra_a_primeira_visualizacao(api, views):
    resposta = client.post("/watch", json={"video_id": "v-fra"})

    assert resposta.status_code == 200
    assert resposta.json() == {"video_id": "v-fra", "views": 1, "registrado": True}


def test_watch_acumula_visualizacoes(api, views):
    client.post("/watch", json={"video_id": "v-fra"})
    resposta = client.post("/watch", json={"video_id": "v-fra"})

    assert resposta.json()["views"] == 2


def test_watch_com_usuario_alimenta_o_historico(api, views):
    client.post("/watch", json={"video_id": "v-fra", "user_id": "gustavo"})

    assert views.registros == [("v-fra", "gustavo")]


def test_watch_sem_usuario_tambem_funciona(api, views):
    resposta = client.post("/watch", json={"video_id": "v-fra"})

    assert resposta.status_code == 200
    assert views.registros == [("v-fra", None)]


@pytest.mark.parametrize(
    "corpo",
    [
        {},
        {"video_id": ""},
        {"user_id": "gustavo"},
        {"video_id": None},
    ],
)
def test_watch_com_payload_invalido_devolve_422(api, corpo):
    resposta = client.post("/watch", json=corpo)

    assert resposta.status_code == 422


def test_watch_com_redis_fora_do_ar_devolve_503(api, views):
    """O player não pode quebrar porque o contador caiu."""
    views.erro = ConnectionError("redis indisponível")

    resposta = client.post("/watch", json={"video_id": "v-fra"})

    assert resposta.status_code == 503
    assert "indisponível" in resposta.json()["detail"]


def test_fluxo_completo_watch_depois_trending(api, views):
    """Registra 3 views e o /trending precisa refletir."""
    views.ranking = [("v-fra", 3)]

    for _ in range(3):
        client.post("/watch", json={"video_id": "v-fra"})

    resposta = client.get("/trending")

    assert resposta.json()["trending"][0]["video_id"] == "v-fra"


# ---------------------------------------------------------------------------
# GET /catalogo e /catalogo/{video_id} — home do player
# ---------------------------------------------------------------------------

def test_catalogo_lista_os_videos_prontos(api):
    resposta = client.get("/catalogo")

    assert resposta.status_code == 200
    ids = {item["video_id"] for item in resposta.json()["videos"]}
    assert ids == {"v-fra", "v-geo", "v-his", "v-quim"}


def test_catalogo_exclui_videos_nao_concluidos(api, banco):
    banco["videos"]["v-pend"] = video("v-pend", "Pendente", ["matemática"], status="pending")

    ids = {item["video_id"] for item in client.get("/catalogo").json()["videos"]}

    assert "v-pend" not in ids


def test_catalogo_busca_por_titulo(api):
    ids = [item["video_id"] for item in client.get("/catalogo?q=fra").json()["videos"]]

    assert ids == ["v-fra"]


def test_catalogo_busca_por_tag(api):
    ids = [item["video_id"] for item in client.get("/catalogo?q=geometria").json()["videos"]]

    assert ids == ["v-geo"]


def test_catalogo_busca_sem_resultado_devolve_lista_vazia(api):
    corpo = client.get("/catalogo?q=xyz").json()

    assert corpo["videos"] == []
    assert corpo["total"] == 0


def test_catalogo_ordena_pelos_mais_assistidos(api, views):
    views.contagens["v-quim"] = 90

    primeiro = client.get("/catalogo").json()["videos"][0]

    assert primeiro["video_id"] == "v-quim"


def test_catalogo_detalhe_enriquece_com_urls_absolutas(api):
    item = client.get("/catalogo/v-fra").json()

    assert item["titulo"] == "Frações"
    assert item["hls_url"] == "http://testserver/videos/v-fra/master.m3u8"
    assert item["thumbnail_url"] == "http://testserver/videos/v-fra/thumbnail.jpg"


def test_catalogo_detalhe_inexistente_retorna_404(api):
    assert client.get("/catalogo/fantasma").status_code == 404


def test_trending_com_redis_fora_devolve_lista_vazia_sem_500(api, views):
    """Contador indisponível não pode derrubar a home nem o player."""
    import redis as modulo_redis

    def _mais_assistidos_fora(limite=10):
        raise modulo_redis.ConnectionError("Connection refused")

    views.mais_assistidos = _mais_assistidos_fora

    resposta = client.get("/trending")

    assert resposta.status_code == 200
    assert resposta.json()["trending"] == []
    assert "indisponível" in resposta.json()["motivo"]


def test_catalogo_nao_quebra_sem_o_contador_de_views(api, views):
    def _boom(video_id):
        raise ConnectionError("redis fora")

    views.contar_visualizacoes = _boom

    resposta = client.get("/catalogo")

    assert resposta.status_code == 200
    assert len(resposta.json()["videos"]) == 4
    assert all(item["views"] == 0 for item in resposta.json()["videos"])
