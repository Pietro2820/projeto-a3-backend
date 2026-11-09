"""
Testes unitários do `status_store` (contrato #5 da documentação técnica).

O contrato diz que o frontend consulta:
    GET /status/{video_id} -> pending | processing | completed | failed

Quem grava/ler esses estados é este módulo, usando Redis como backend.
Os testes NÃO sobem um Redis de verdade: usamos um dublê em memória
(`FakeRedis`) trocado via monkeypatch — assim a suíte continua rápida,
determinística e rodando em qualquer máquina (inclusive no CI sem serviço).

Isso é TDD de verdade: o comportamento esperado do store está descrito aqui
antes de qualquer preocupação de infraestrutura.
"""
from __future__ import annotations

import json

import pytest

from app.queue import status_store


class FakeRedis:
    """Dublê de `redis.Redis` com a superfície usada pelo status_store.

    Implementa só `set`/`get` (com `decode_responses=True`, como o cliente
    real é criado no módulo) guardando tudo num dicionário.
    """

    def __init__(self) -> None:
        self.dados: dict[str, str] = {}

    def set(self, chave: str, valor: str) -> bool:
        self.dados[chave] = valor
        return True

    def get(self, chave: str) -> str | None:
        return self.dados.get(chave)


@pytest.fixture
def fake_redis(monkeypatch) -> FakeRedis:
    """Troca o cliente Redis do módulo pelo dublê em memória."""
    falso = FakeRedis()
    monkeypatch.setattr(status_store, "_redis_client", falso)
    return falso


# ---------------------------------------------------------------------------
# Escrita (set_status)
# ---------------------------------------------------------------------------

def test_set_status_grava_json_com_video_id_e_status(fake_redis):
    status_store.set_status("abc-123", "pending")

    gravado = json.loads(fake_redis.dados["video_status:abc-123"])
    assert gravado["video_id"] == "abc-123"
    assert gravado["status"] == "pending"


def test_chave_gravada_usa_o_prefixo_do_modulo(fake_redis):
    status_store.set_status("abc-123", "pending")

    chave_esperada = f"{status_store.STATUS_KEY_PREFIX}abc-123"
    assert list(fake_redis.dados) == [chave_esperada]
    assert chave_esperada == "video_status:abc-123"


def test_valor_gravado_e_string_json_e_nao_dict(fake_redis):
    """Redis guarda bytes/str — se gravar dict direto, o get_status quebra."""
    status_store.set_status("abc-123", "processing")

    assert isinstance(fake_redis.dados["video_status:abc-123"], str)


def test_set_status_sem_extra_grava_apenas_os_campos_base(fake_redis):
    status_store.set_status("abc-123", "processing")

    gravado = json.loads(fake_redis.dados["video_status:abc-123"])
    assert gravado == {"video_id": "abc-123", "status": "processing"}


def test_extra_vazio_nao_acrescenta_campos(fake_redis):
    status_store.set_status("abc-123", "processing", extra={})

    gravado = json.loads(fake_redis.dados["video_status:abc-123"])
    assert set(gravado) == {"video_id", "status"}


def test_set_status_com_extra_acrescenta_os_campos(fake_redis):
    status_store.set_status(
        "abc-123", "completed", {"caminho_hls": "videos/abc-123/master.m3u8"}
    )

    gravado = json.loads(fake_redis.dados["video_status:abc-123"])
    assert gravado["caminho_hls"] == "videos/abc-123/master.m3u8"
    assert gravado["status"] == "completed"


def test_set_status_sobrescreve_o_valor_anterior(fake_redis):
    status_store.set_status("abc-123", "pending")
    status_store.set_status("abc-123", "failed", {"erro": "FFmpeg caiu"})

    assert len(fake_redis.dados) == 1
    gravado = json.loads(fake_redis.dados["video_status:abc-123"])
    assert gravado["status"] == "failed"


def test_acentos_e_unicode_sao_preservados(fake_redis):
    """Mensagens de erro vêm em português — não podem virar \\u00e7."""
    status_store.set_status("abc-123", "failed", {"erro": "vídeo inválido — ção"})

    gravado = json.loads(fake_redis.dados["video_status:abc-123"])
    assert gravado["erro"] == "vídeo inválido — ção"


# ---------------------------------------------------------------------------
# Leitura (get_status)
# ---------------------------------------------------------------------------

def test_get_status_devolve_none_para_video_desconhecido(fake_redis):
    assert status_store.get_status("nao-existe") is None


def test_ida_e_volta_preserva_video_id_e_status(fake_redis):
    status_store.set_status("abc-123", "completed")

    assert status_store.get_status("abc-123") == {
        "video_id": "abc-123",
        "status": "completed",
    }


def test_get_status_devolve_o_caminho_hls_gravado_pela_task(fake_redis):
    status_store.set_status(
        "abc-123", "completed", {"caminho_hls": "videos/abc-123/master.m3u8"}
    )

    lido = status_store.get_status("abc-123")
    assert lido["caminho_hls"] == "videos/abc-123/master.m3u8"


def test_get_status_devolve_dict_e_nao_string(fake_redis):
    """O router serializa isso como JSON — precisa virar dict, não str."""
    status_store.set_status("abc-123", "pending")

    assert isinstance(status_store.get_status("abc-123"), dict)


# ---------------------------------------------------------------------------
# Fluxo completo (ciclo de vida do processamento)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "estado",
    ["pending", "processing", "completed", "failed"],
)
def test_os_quatro_estados_do_contrato_sao_aceitos(fake_redis, estado):
    status_store.set_status("abc-123", estado)

    assert status_store.get_status("abc-123")["status"] == estado


def test_ciclo_de_vida_completo_do_processamento(fake_redis):
    """pending -> processing -> completed, como o worker real executa."""
    status_store.set_status("abc-123", "pending")
    assert status_store.get_status("abc-123")["status"] == "pending"

    status_store.set_status("abc-123", "processing")
    assert status_store.get_status("abc-123")["status"] == "processing"

    status_store.set_status(
        "abc-123", "completed", {"caminho_hls": "videos/abc-123/master.m3u8"}
    )
    final = status_store.get_status("abc-123")
    assert final["status"] == "completed"
    assert final["caminho_hls"] == "videos/abc-123/master.m3u8"


def test_videos_diferentes_tem_status_independentes(fake_redis):
    status_store.set_status("video-1", "completed")
    status_store.set_status("video-2", "failed", {"erro": "arquivo corrompido"})

    assert status_store.get_status("video-1")["status"] == "completed"
    assert status_store.get_status("video-2")["status"] == "failed"
    assert len(fake_redis.dados) == 2
