"""
Exceções específicas do módulo de transcodificação.

Ter erros próprios (em vez de Exception genérica) atende ao critério de
"tratamento de erros" do edital e permite que a task do Celery (Pietro)
distinga o que é erro de vídeo inválido, FFmpeg ausente ou falha de storage.
"""


class TranscodingError(Exception):
    """Erro base do módulo de transcodificação.

    Todo erro esperado deste módulo herda dela — quem chama pode fazer
    `except TranscodingError` e capturar tudo de uma vez.
    """


class FFmpegNaoEncontradoError(TranscodingError):
    """FFmpeg/ffprobe não está instalado ou não está no PATH do sistema."""


class VideoInvalidoError(TranscodingError):
    """O vídeo de entrada não existe, está corrompido ou não é um vídeo legível."""


class StorageError(TranscodingError):
    """Falha na integração com o armazenamento externo (AWS S3 / MinIO)."""
