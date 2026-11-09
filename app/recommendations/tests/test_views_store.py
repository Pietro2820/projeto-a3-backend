"""
Testes do contador de visualizações em Redis (`views_store.py`).

Usa um dublê de Redis em memória — os mesmos motivos do `test_status_store.py`:
suíte rápida, determinística e independente de serviço no ar.
"""
from __future__ import annotations

import pytest

from app.recommendations import views_store


class FakeRedis:
    """Dublê de `redis.Redis` com a superfície usada pelo views_store."""

    def __init__(self) -> None:
        self.strings: dict[str, str] = {}
        self.listas: dict[str, list[str]] = {}
        self.zsets: dict[str, dict[str, float]] = {}

    # --- strings / contadores ---
    def incr(self, chave: str) -> int:
        atual = int(self.strings.get(chave, "0")) + 1
        self.strings[chave] = str(atual)
        return atual

    def get(self, chave: str):
        return self.strings.get(chave)

    # --- sorted set (ranking) ---
    def zincrby(self, chave: str, incremento: float, membro: str) -> float:
        conjunto = self.zsets.setdefault(chave, {})
        conjunto[membro] = conjunto.get(membro, 0.0) + incremento
        return conjunto[membro]

    def zrevrange(self, chave: str, inicio: int, fim: int, withscores: bool = False):
        conjunto = self.zsets.get(chave, {})
        ordenado = sorted(conjunto.items(), key=lambda item: (-item[1], item[0]))
        fatia = ordenado[inicio: fim + 1]
        if withscores:
            return fatia
        return [membro for membro, _ in fatia]

    # --- listas (histórico) ---
    def lpush(self, chave: str, valor: str) -> int:
        lista = self.listas.setdefault(chave, [])
        lista.insert(0, valor)
        return len(lista)

    def lrem(self, chave: str, contagem: int, valor: str) -> int:
        lista = self.listas.get(chave, [])
        removidos = lista.count(valor)
        self.listas[chave] = [item for item in lista if item != valor]
        return removidos

    def ltrim(self, chave: str, inicio: int, fim: int) -> bool:
        if chave in self.listas:
            self.listas[chave] = self.listas[chave][inicio: fim + 1]
        return True

    def lrange(self, chave: str, inicio: int, fim: int):
        lista = self.listas.get(chave, [])
        if fim == -1:
            return lista[inicio:]
        return lista[inicio: fim + 1]


@pytest.fixture
def fake_redis(monkeypatch) -> FakeRedis:
    falso = FakeRedis()
    monkeypatch.setattr(views_store, "_redis_client", falso)
    return falso


# ---------------------------------------------------------------------------
# Contagem
# ---------------------------------------------------------------------------

def test_primeira_visualizacao_devolve_um(fake_redis):
    assert views_store.registrar_visualizacao("v1") == 1


def test_visualizacoes_acumulam(fake_redis):
    views_store.registrar_visualizacao("v1")
    views_store.registrar_visualizacao("v1")

    assert views_store.registrar_visualizacao("v1") == 3


def test_contar_video_nunca_assistido_devolve_zero(fake_redis):
    assert views_store.contar_visualizacoes("inexistente") == 0


def test_contagem_e_independente_por_video(fake_redis):
    views_store.registrar_visualizacao("v1")
    views_store.registrar_visualizacao("v1")
    views_store.registrar_visualizacao("v2")

    assert views_store.contar_visualizacoes("v1") == 2
    assert views_store.contar_visualizacoes("v2") == 1


# ---------------------------------------------------------------------------
# Ranking (/trending)
# ---------------------------------------------------------------------------

def test_ranking_vem_do_mais_assistido_para_o_menos(fake_redis):
    for _ in range(3):
        views_store.registrar_visualizacao("v1")
    views_store.registrar_visualizacao("v2")
    for _ in range(5):
        views_store.registrar_visualizacao("v3")

    assert views_store.mais_assistidos(3) == [("v3", 5), ("v1", 3), ("v2", 1)]


def test_ranking_sem_visualizacoes_devolve_lista_vazia(fake_redis):
    assert views_store.mais_assistidos(10) == []


def test_ranking_respeita_o_limite(fake_redis):
    for indice in range(5):
        views_store.registrar_visualizacao(f"v{indice}")

    assert len(views_store.mais_assistidos(2)) == 2


def test_ranking_com_limite_zero_nao_quebra(fake_redis):
    views_store.registrar_visualizacao("v1")

    assert views_store.mais_assistidos(0) == []


def test_empate_no_ranking_nao_levanta_erro(fake_redis):
    views_store.registrar_visualizacao("v1")
    views_store.registrar_visualizacao("v2")

    ranking = views_store.mais_assistidos(10)

    assert len(ranking) == 2
    assert {video_id for video_id, _ in ranking} == {"v1", "v2"}


# ---------------------------------------------------------------------------
# Histórico (/recomendacoes)
# ---------------------------------------------------------------------------

def test_historico_de_usuario_sem_views_esta_vazio(fake_redis):
    assert views_store.historico_do_usuario("ninguem") == []


def test_historico_guarda_o_video_assistido(fake_redis):
    views_store.registrar_visualizacao("v1", user_id="gustavo")

    assert views_store.historico_do_usuario("gustavo") == ["v1"]


def test_historico_vem_do_mais_recente_para_o_mais_antigo(fake_redis):
    for video_id in ("v1", "v2", "v3"):
        views_store.registrar_visualizacao(video_id, user_id="gustavo")

    assert views_store.historico_do_usuario("gustavo") == ["v3", "v2", "v1"]


def test_reassistir_nao_duplica_no_historico(fake_redis):
    """Sem isso, um vídeo repetido dominaria as recomendações do usuário."""
    views_store.registrar_visualizacao("v1", user_id="gustavo")
    views_store.registrar_visualizacao("v2", user_id="gustavo")
    views_store.registrar_visualizacao("v1", user_id="gustavo")

    assert views_store.historico_do_usuario("gustavo") == ["v1", "v2"]
    assert views_store.contar_visualizacoes("v1") == 2


def test_historicos_de_usuarios_diferentes_sao_isolados(fake_redis):
    views_store.registrar_visualizacao("v1", user_id="gustavo")
    views_store.registrar_visualizacao("v2", user_id="pedro")

    assert views_store.historico_do_usuario("gustavo") == ["v1"]
    assert views_store.historico_do_usuario("pedro") == ["v2"]


def test_historico_tem_tamanho_limitado(fake_redis):
    for indice in range(views_store.TAMANHO_HISTORICO + 10):
        views_store.registrar_visualizacao(f"v{indice}", user_id="gustavo")

    assert len(views_store.historico_do_usuario("gustavo")) == views_store.TAMANHO_HISTORICO


def test_view_sem_usuario_conta_mas_nao_cria_historico(fake_redis):
    views_store.registrar_visualizacao("v1")

    assert views_store.contar_visualizacoes("v1") == 1
    assert fake_redis.listas == {}
