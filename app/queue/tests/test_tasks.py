"""
Testes unitários das tasks Celery (contrato #3 da documentação técnica).

Contrato combinado entre a fila (Pietro) e a transcodificação (João):
    entrada -> video_id, caminho do arquivo original
    saida   -> status (completed / failed) + caminho dos arquivos HLS gerados

Estratégia de teste (TDD sem infraestrutura):
  * As tasks são executadas com `.apply()`, que roda em modo "eager"
    (na mesma thread, sem publicar no broker). Nenhum teste precisa de
    Redis nem de worker no ar.
  * `processar` (módulo do João) e `set_status` (Redis) são substituídos por
    dublês — o que está em teste aqui é a LÓGICA da task: repasse de
    argumentos, gravação de status, retry com backoff e tratamento de erro.
  * `pytest.raises`/`throw=False` cobrem o critério "tratamento de erros".
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.queue import tasks


class RetryInterrompido(Exception):
    """Sentinela: interrompe a task assim que ela pede retry."""


@pytest.fixture
def resultado_ok() -> dict:
    """Saída de sucesso no formato exato do contrato #3."""
    return {
        "status": "completed",
        "video_id": "abc-123",
        "diretorio": "videos/abc-123",
        "master_playlist": "videos/abc-123/master.m3u8",
        "caminho_hls": "videos/abc-123/master.m3u8",
        "thumbnail": "videos/abc-123/thumbnail.jpg",
    }


@pytest.fixture
def resultado_falha() -> dict:
    """Saída de falha no formato exato do contrato #3."""
    return {
        "status": "failed",
        "video_id": "abc-123",
        "error": "FFmpeg não está instalado no sistema",
    }


# ---------------------------------------------------------------------------
# hello_world — smoke test da integração Celery
# ---------------------------------------------------------------------------

def test_hello_world_retorna_pong():
    resultado = tasks.hello_world.apply()

    assert resultado.successful()
    assert resultado.result == "pong"


# ---------------------------------------------------------------------------
# Registro / nomenclatura das tasks
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "nome_da_task",
    [
        "app.queue.tasks.hello_world",
        "app.queue.tasks.processar_upload",
        "app.queue.tasks.transcodificar_video",
        "app.queue.tasks.notificar_status",
    ],
)
def test_task_registrada_com_nome_explicito(nome_da_task):
    """Nomes explícitos sobrevivem a rename de módulo/patch no CI."""
    assert nome_da_task in tasks.celery_app.tasks


def test_transcodificar_video_tem_tres_tentativas():
    assert tasks.transcodificar_video.max_retries == 3


# ---------------------------------------------------------------------------
# transcodificar_video — caminho feliz
# ---------------------------------------------------------------------------

def test_transcodificar_devolve_o_resultado_do_modulo_de_transcodificacao(resultado_ok):
    with patch.object(tasks, "processar", return_value=resultado_ok), \
         patch.object(tasks, "set_status"):
        resultado = tasks.transcodificar_video.apply(args=("abc-123", "/uploads/abc-123/original.mp4"))

    assert resultado.successful()
    assert resultado.result == resultado_ok


def test_argumentos_sao_repassados_ao_modulo_de_transcodificacao():
    with patch.object(tasks, "processar", return_value={"status": "completed", "caminho_hls": "h"}) as mock_processar, \
         patch.object(tasks, "set_status"):
        tasks.transcodificar_video.apply(args=("abc-123", "/uploads/abc-123/original.mp4"))

    mock_processar.assert_called_once_with("abc-123", "/uploads/abc-123/original.mp4")


def test_transcodificar_grava_status_completed_com_caminho_hls(resultado_ok):
    with patch.object(tasks, "processar", return_value=resultado_ok), \
         patch.object(tasks, "set_status") as mock_set_status:
        tasks.transcodificar_video.apply(args=("abc-123", "/uploads/abc-123/original.mp4"))

    mock_set_status.assert_called_once_with(
        "abc-123", "completed", {"caminho_hls": "videos/abc-123/master.m3u8"}
    )


def test_transcodificar_com_sucesso_nao_pede_retry(resultado_ok):
    with patch.object(tasks, "processar", return_value=resultado_ok), \
         patch.object(tasks, "set_status"), \
         patch.object(tasks.transcodificar_video, "retry", side_effect=RetryInterrompido) as mock_retry:
        tasks.transcodificar_video.apply(args=("abc-123", "/x.mp4"), throw=False)

    assert mock_retry.called is False


# ---------------------------------------------------------------------------
# transcodificar_video — falha dentro do contrato (status="failed")
# ---------------------------------------------------------------------------

def test_transcodificar_grava_status_failed_com_a_mensagem_de_erro(resultado_falha):
    with patch.object(tasks, "processar", return_value=resultado_falha), \
         patch.object(tasks, "set_status") as mock_set_status:
        tasks.transcodificar_video.apply(args=("abc-123", "/x.mp4"), throw=False)

    primeira_chamada = mock_set_status.call_args_list[0]
    assert primeira_chamada.args == (
        "abc-123",
        "failed",
        {"erro": "FFmpeg não está instalado no sistema"},
    )


def test_transcodificar_com_falha_pede_retry_com_backoff_de_60s(resultado_falha):
    with patch.object(tasks, "processar", return_value=resultado_falha), \
         patch.object(tasks, "set_status"), \
         patch.object(tasks.transcodificar_video, "retry", side_effect=RetryInterrompido) as mock_retry:
        tasks.transcodificar_video.apply(args=("abc-123", "/x.mp4"), throw=False)

    assert mock_retry.call_count == 1
    assert mock_retry.call_args.kwargs["countdown"] == 60
    assert isinstance(mock_retry.call_args.kwargs["exc"], RuntimeError)


def test_transcodificar_esgota_as_tentativas_e_devolve_a_falha(resultado_falha):
    """Sem retry disponível (eager esgota as 3+1 tentativas) a task encerra
    devolvendo o resultado 'failed' — não explode no worker."""
    with patch.object(tasks, "processar", return_value=resultado_falha) as mock_processar, \
         patch.object(tasks, "set_status") as mock_set_status:
        resultado = tasks.transcodificar_video.apply(args=("abc-123", "/x.mp4"), throw=False)

    assert resultado.successful()
    assert resultado.result == resultado_falha
    assert mock_processar.call_count == 4          # 1 tentativa + 3 retries
    assert mock_set_status.call_count == 4


# ---------------------------------------------------------------------------
# transcodificar_video — erro inesperado FORA do contrato
# ---------------------------------------------------------------------------

def test_erro_inesperado_encerra_a_task_com_failure():
    with patch.object(tasks, "processar", side_effect=ImportError("módulo quebrado")), \
         patch.object(tasks, "set_status"):
        resultado = tasks.transcodificar_video.apply(args=("abc-123", "/x.mp4"), throw=False)

    assert resultado.failed()
    assert isinstance(resultado.result, ImportError)


def test_erro_inesperado_grava_failed_na_ultima_tentativa():
    with patch.object(tasks, "processar", side_effect=ValueError("kabum")), \
         patch.object(tasks, "set_status") as mock_set_status:
        tasks.transcodificar_video.apply(args=("abc-123", "/x.mp4"), throw=False)

    mock_set_status.assert_called_once_with("abc-123", "failed", {"erro": "kabum"})


def test_erro_inesperado_tambem_pede_retry_com_backoff():
    with patch.object(tasks, "processar", side_effect=ValueError("kabum")), \
         patch.object(tasks, "set_status"), \
         patch.object(tasks.transcodificar_video, "retry", side_effect=RetryInterrompido) as mock_retry:
        tasks.transcodificar_video.apply(args=("abc-123", "/x.mp4"), throw=False)

    assert mock_retry.call_args.kwargs["countdown"] == 60
    assert isinstance(mock_retry.call_args.kwargs["exc"], ValueError)


# ---------------------------------------------------------------------------
# processar_upload — ponte entre o endpoint /upload e a transcodificação
# ---------------------------------------------------------------------------

def test_processar_upload_grava_status_processing():
    with patch.object(tasks, "set_status") as mock_set_status, \
         patch.object(tasks.transcodificar_video, "delay"):
        tasks.processar_upload.apply(args=("abc-123", "/uploads/abc-123/original.mp4"))

    mock_set_status.assert_called_once_with("abc-123", "processing")


def test_processar_upload_dispara_a_transcodificacao_com_os_mesmos_argumentos():
    with patch.object(tasks, "set_status"), \
         patch.object(tasks.transcodificar_video, "delay") as mock_delay:
        tasks.processar_upload.apply(args=("abc-123", "/uploads/abc-123/original.mp4"))

    mock_delay.assert_called_once_with("abc-123", "/uploads/abc-123/original.mp4")


def test_processar_upload_retorna_resposta_imediata_sem_esperar_o_video():
    """O usuário recebe 'processing' na hora — é isso que torna o fluxo assíncrono."""
    with patch.object(tasks, "set_status"), \
         patch.object(tasks.transcodificar_video, "delay"):
        resultado = tasks.processar_upload.apply(args=("abc-123", "/x.mp4"))

    assert resultado.result == {"video_id": "abc-123", "status": "processing"}


# ---------------------------------------------------------------------------
# notificar_status
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("estado", ["pending", "processing", "completed", "failed"])
def test_notificar_status_devolve_o_payload_do_evento(estado):
    resultado = tasks.notificar_status.apply(args=("abc-123", estado))

    assert resultado.result == {"video_id": "abc-123", "status": estado}
