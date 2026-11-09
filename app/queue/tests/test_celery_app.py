"""
Testes unitários da configuração do Celery (`celery_app.py`).

Aqui o que se testa não é o Redis, e sim a CONFIGURAÇÃO da fila: as três
filas do contrato, o roteamento de cada task e os ajustes de confiabilidade
(`task_acks_late` + `prefetch_multiplier=1`) que impedem perda de vídeo
quando um worker cai no meio da transcodificação.

Nenhum teste conecta no broker — só inspecionamos `celery_app.conf`.
"""
from __future__ import annotations

import pytest

from app.queue import celery_app as modulo_celery
from app.queue.celery_app import celery_app


def nomes_das_filas() -> set[str]:
    return {fila.name for fila in celery_app.conf.task_queues}


# ---------------------------------------------------------------------------
# Filas declaradas
# ---------------------------------------------------------------------------

def test_as_tres_filas_do_contrato_estao_declaradas():
    assert nomes_das_filas() == {"uploads", "transcode", "notifications"}


def test_fila_padrao_e_uploads():
    assert celery_app.conf.task_default_queue == "uploads"


def test_exchange_padrao_e_do_tipo_direct():
    assert celery_app.conf.task_default_exchange == "default"
    assert celery_app.conf.task_default_exchange_type == "direct"


def test_chave_de_roteamento_padrao_e_uploads():
    assert celery_app.conf.task_default_routing_key == "uploads"


# ---------------------------------------------------------------------------
# Roteamento por task
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "task,fila_esperada",
    [
        ("app.queue.tasks.processar_upload", "uploads"),
        ("app.queue.tasks.transcodificar_video", "transcode"),
        ("app.queue.tasks.notificar_status", "notifications"),
    ],
)
def test_task_roteada_para_a_fila_correta(task, fila_esperada):
    rota = celery_app.conf.task_routes[task]

    assert rota["queue"] == fila_esperada
    assert rota["routing_key"] == fila_esperada


def test_todas_as_filas_declaradas_tem_alguma_rota():
    """Fila declarada e nunca usada = configuração morta (cheiro de projeto)."""
    filas_roteadas = {rota["queue"] for rota in celery_app.conf.task_routes.values()}

    assert filas_roteadas == nomes_das_filas()


# ---------------------------------------------------------------------------
# Confiabilidade do worker
# ---------------------------------------------------------------------------

def test_acks_late_habilitado_para_nao_perder_task():
    """Com acks_late, se o worker morrer a task volta para a fila."""
    assert celery_app.conf.task_acks_late is True


def test_prefetch_multiplier_um_evita_fila_individual_no_worker():
    """Transcodificar é pesado: 1 task por vez evita vídeo parado em memória."""
    assert celery_app.conf.worker_prefetch_multiplier == 1


# ---------------------------------------------------------------------------
# Conexão / include
# ---------------------------------------------------------------------------

def test_broker_e_backend_usam_esquema_redis():
    assert modulo_celery.BROKER_URL.startswith("redis://")
    assert modulo_celery.RESULT_BACKEND.startswith("redis://")


def test_broker_e_backend_vem_das_variaveis_de_ambiente():
    """As constantes do módulo são exatamente o que o Celery usa."""
    assert celery_app.conf.broker_url == modulo_celery.BROKER_URL
    assert celery_app.conf.result_backend == modulo_celery.RESULT_BACKEND


def test_modulo_de_tasks_esta_no_include():
    """Garante que as tasks são registradas quando o worker sobe."""
    assert "app.queue.tasks" in celery_app.conf.include


def test_nome_do_app_e_projeto_a3():
    assert celery_app.main == "projeto_a3"
