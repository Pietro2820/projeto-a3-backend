"""
Acesso à tabela `usuarios` — autenticação SOMENTE via Supabase.

Diferente dos vídeos (que têm fallback em JSON local para rodar sem rede),
usuário não tem fallback: senha hasheada mora em banco de verdade. Sem
SUPABASE_URL/KEY no .env as rotas de login/cadastro respondem 503 com
mensagem clara, e o front mostra o aviso — não existe "usuário de mentira".

Pré-requisito: rodar `supabase/schema_usuarios.sql` no SQL Editor do
Supabase (cria a tabela `public.usuarios`).
"""
from __future__ import annotations

import logging

from app.auth.senhas import gerar_hash
from app.database.client import get_client

logger = logging.getLogger(__name__)

TABLE = "usuarios"

# Colunas que saem do banco — a senha_hash NUNCA sai daqui para o cliente.
COLUNAS_PUBLICAS = "usuario_id,nome,email,criado_em"


class EmailDuplicadoError(Exception):
    """E-mail já cadastrado — a rota traduz para HTTP 409."""


def _cliente():
    """Client do Supabase ou RuntimeError com mensagem acionável."""
    return get_client()


def buscar_por_email(email: str) -> dict | None:
    """Procura o usuário pelo e-mail (normalizado em minúsculas).

    Devolve o registro COMPLETO (com senha_hash — quem usa é o login para
    verificar a senha) ou None quando não existe.
    """
    client = _cliente()
    response = (
        client.table(TABLE)
        .select("*")
        .eq("email", email.strip().lower())
        .limit(1)
        .execute()
    )
    return response.data[0] if response.data else None


def criar_usuario(nome: str, email: str, senha: str) -> dict:
    """Insere um usuário novo com a senha hasheada (PBKDF2).

    Levanta EmailDuplicadoError quando o e-mail já existe (unique no banco).
    A resposta nunca inclui o hash — só os campos públicos.
    """
    client = _cliente()
    payload = {
        "nome": nome.strip(),
        "email": email.strip().lower(),
        "senha_hash": gerar_hash(senha),
    }

    try:
        response = client.table(TABLE).insert(payload).execute()
    except Exception as exc:  # postgrest não tem exceção estável entre versões
        mensagem = str(exc).lower()
        if "duplicate key" in mensagem or "unique" in mensagem or "23505" in mensagem:
            raise EmailDuplicadoError(payload["email"]) from exc
        raise

    dados = response.data[0]
    return {coluna: dados[coluna] for coluna in COLUNAS_PUBLICAS.split(",")}
