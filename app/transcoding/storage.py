"""
Integração com armazenamento externo: AWS S3 / MinIO (Passo 6 do guia).

Cobre o critério de "integração com serviço externo" do edital. Como o MinIO
é compatível com a API do S3, tudo funciona via boto3 — a única diferença é
apontar AWS_ENDPOINT_URL para o MinIO (ex: http://localhost:9000).

Escopo (importante): este módulo guarda SOMENTE arquivos de vídeo (original +
processados). Metadados (título, tags, status) ficam no Supabase e são
responsabilidade do Pietro/Rafael.

Variáveis de ambiente:
    S3_BUCKET               nome do bucket (obrigatória para usar S3)
    AWS_ENDPOINT_URL        endpoint do MinIO (não definir para AWS S3 real)
    AWS_ACCESS_KEY_ID       credenciais (no .env, NUNCA commitadas)
    AWS_SECRET_ACCESS_KEY
    AWS_REGION              região (padrão: us-east-1)
    S3_PUBLIC_URL           (opcional) base pública p/ URLs, ex: um CDN

Chaves usadas (contrato #1 da documentação técnica):
    uploads/{video_id}/original.{ext}          <- arquivo original
    videos/{video_id}/{resolucao}/playlist.m3u8
    videos/{video_id}/{resolucao}/segment_NNN.ts
    videos/{video_id}/master.m3u8
    videos/{video_id}/thumbnail.jpg
"""
from __future__ import annotations

import os
from pathlib import Path

from app.transcoding.errors import StorageError

try:
    import boto3
    from botocore.config import Config as _BotoConfig
    from botocore.exceptions import BotoCoreError, ClientError
except ImportError:  # boto3 é opcional em dev local sem S3 — o resto do módulo funciona
    boto3 = None
    _BotoConfig = None
    BotoCoreError = ClientError = Exception


# Content-Type correto é essencial: se o .m3u8 for servido como
# application/octet-stream, alguns players/CDNs se recusam a tocar.
CONTENT_TYPES = {
    ".m3u8": "application/vnd.apple.mpegurl",
    ".ts": "video/mp2t",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}


def content_type_de(caminho: str | Path) -> str:
    """Content-Type pelo sufixo do arquivo (desconhecido -> octet-stream)."""
    return CONTENT_TYPES.get(Path(caminho).suffix.lower(), "application/octet-stream")


class VideoStorage:
    """Cliente de armazenamento de vídeos (S3 ou MinIO).

    Uso típico:
        storage = VideoStorage.from_env()
        storage.upload_original(video_id, "uploads/abc/original.mp4")
        storage.upload_processados(video_id, "videos/abc")
    """

    def __init__(
        self,
        bucket: str,
        client=None,
        endpoint_url: str | None = None,
        region: str | None = None,
        public_url: str | None = None,
    ) -> None:
        if client is None and boto3 is None:
            raise StorageError(
                "boto3 não está instalado. Rode: pip install boto3"
            )
        self.bucket = bucket
        self.endpoint_url = (endpoint_url or os.getenv("AWS_ENDPOINT_URL") or "").rstrip("/") or None
        self.region = region or os.getenv("AWS_REGION", "us-east-1")
        self.public_url = (public_url or os.getenv("S3_PUBLIC_URL") or "").rstrip("/") or None

        if client is not None:
            self._client = client
        else:
            # Path-style é o recomendado para MinIO (e inofensivo na AWS).
            config = _BotoConfig(s3={"addressing_style": "path"}) if self.endpoint_url else None
            self._client = boto3.client(
                "s3",
                endpoint_url=self.endpoint_url,
                region_name=self.region,
                config=config,
            )

    @classmethod
    def from_env(cls) -> "VideoStorage":
        """Cria o storage a partir das variáveis de ambiente (.env)."""
        bucket = os.getenv("S3_BUCKET")
        if not bucket:
            raise StorageError(
                "Variável S3_BUCKET não configurada. Copie .env.example para .env "
                "e preencha as credenciais do S3/MinIO (veja o README do módulo)."
            )
        return cls(bucket=bucket)

    # -- uploads ------------------------------------------------------------
    def upload_arquivo(self, caminho_local: str | Path, chave: str) -> str:
        """Faz upload de um arquivo local para a chave S3 informada.

        Returns:
            A própria chave (útil para encadear e montar URLs).
        """
        local = Path(caminho_local)
        if not local.is_file():
            raise StorageError(f"Arquivo para upload não existe: '{local}'")
        try:
            self._client.upload_file(
                str(local),
                self.bucket,
                chave,
                ExtraArgs={"ContentType": content_type_de(local)},
            )
        except (ClientError, BotoCoreError) as exc:
            raise StorageError(f"Falha no upload para S3 ('{local}' -> '{chave}'): {exc}") from exc
        return chave

    def upload_original(self, video_id: str, caminho_original: str | Path) -> str:
        """Sobe o arquivo original — chave no formato do contrato #1."""
        extensao = Path(caminho_original).suffix.lower() or ".mp4"
        return self.upload_arquivo(caminho_original, f"uploads/{video_id}/original{extensao}")

    def upload_processados(self, video_id: str, diretorio_local: str | Path) -> list[str]:
        """Sobe a pasta videos/{video_id}/ inteira, preservando a estrutura.

        Returns:
            Lista de chaves enviadas (ordenadas).
        """
        diretorio = Path(diretorio_local)
        if not diretorio.is_dir():
            raise StorageError(f"Diretório de processados não existe: '{diretorio}'")

        chaves: list[str] = []
        for arquivo in sorted(diretorio.rglob("*")):
            if arquivo.is_file():
                relativa = arquivo.relative_to(diretorio).as_posix()
                chaves.append(self.upload_arquivo(arquivo, f"videos/{video_id}/{relativa}"))
        if not chaves:
            raise StorageError(f"Nenhum arquivo encontrado para upload em '{diretorio}'")
        return chaves

    # -- consultas / URLs ---------------------------------------------------
    def existe(self, chave: str) -> bool:
        """True se a chave existe no bucket."""
        try:
            self._client.head_object(Bucket=self.bucket, Key=chave)
            return True
        except Exception:  # ClientError 404 (ou qualquer falha de rede)
            return False

    def url_publica(self, chave: str) -> str:
        """URL de leitura direta (MinIO path-style, AWS virtual-host ou CDN)."""
        if self.public_url:
            return f"{self.public_url}/{chave}"
        if self.endpoint_url:
            return f"{self.endpoint_url}/{self.bucket}/{chave}"
        return f"https://{self.bucket}.s3.{self.region}.amazonaws.com/{chave}"

    def url_assinada(self, chave: str, expira_em_segundos: int = 3600) -> str:
        """URL temporária com permissão de leitura (para buckets privados)."""
        try:
            return self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": chave},
                ExpiresIn=expira_em_segundos,
            )
        except (ClientError, BotoCoreError) as exc:
            raise StorageError(f"Falha ao gerar URL assinada para '{chave}': {exc}") from exc
