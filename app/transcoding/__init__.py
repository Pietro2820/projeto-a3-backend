"""
Módulo de transcodificação — feature/transcodificacao (João).

Transforma o vídeo cru recebido no upload em streaming adaptativo de verdade:
múltiplas resoluções (360p/480p/720p/1080p) empacotadas em HLS (.m3u8 + .ts),
master playlist e thumbnail — com armazenamento local ou S3/MinIO.

Uso pela task Celery do Pietro:
    from app.transcoding import executar_transcodificacao
    resultado = executar_transcodificacao(video_id, caminho_original)
"""

from app.transcoding.task_adapter import executar_transcodificacao

# Apelido do contrato com a fila: a task do Pietro (app/queue/tasks.py)
# chama `processar(video_id, caminho_original)` — exatamente a assinatura
# que o placeholder dele já previa.
processar = executar_transcodificacao

__all__ = ["executar_transcodificacao", "processar"]
