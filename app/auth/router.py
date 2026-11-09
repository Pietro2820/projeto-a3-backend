"""
Rotas de autenticação — POST /login e POST /cadastro.

A tela de login da branch frontend-css enviava os dados simulados
(setTimeout + redirect); este router é o "TODO(integração)" que o
js/login.js marcava: agora o front chama a API de verdade e o backend
valida as credenciais contra a tabela `usuarios` do Supabase.

Contrato (o que o front espera — não mudar sem atualizar js/login.js):

  POST /cadastro  {nome, email, senha}  -> 201 {usuario:{usuario_id,nome,email}}
                                        -> 409 e-mail já cadastrado
                                        -> 422 validação (Pydantic)
                                        -> 503 banco indisponível/mal configurado
  POST /login     {email, senha}        -> 200 {usuario:{usuario_id,nome,email}}
                                        -> 401 credenciais erradas (MESMA
                                           mensagem para e-mail inexistente e
                                           senha errada — não revela quais
                                           e-mails têm conta)
                                        -> 503 banco indisponível/mal configurado

Sessão pós-login: o front guarda o nome em sessionStorage e redireciona para
home.html. Não há token/JWT de propósito — é o escopo mínimo da etapa; a
evolução natural (proteger /upload, /watch etc.) ficaria para uma próxima.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.auth import usuarios
from app.auth.schemas import CadastroRequest, LoginRequest
from app.auth.senhas import verificar_senha

logger = logging.getLogger(__name__)

router = APIRouter()


def _erro_503(exc: Exception) -> HTTPException:
    """Traduz falha de infraestrutura em 503 com mensagem acionável.

    Cobre os dois casos reais do dia a dia: .env sem SUPABASE_URL/KEY, e o
    SQL `schema_usuarios.sql` ainda não rodado (tabela não existe).
    """
    logger.warning("Banco indisponível na autenticação: %s", exc)
    return HTTPException(
        status_code=503,
        detail=(
            "Banco de dados indisponível. Verifique se SUPABASE_URL/SUPABASE_KEY "
            "estão no .env e se o SQL supabase/schema_usuarios.sql já foi rodado "
            "no SQL Editor do Supabase."
        ),
    )


def _usuario_publico(registro: dict) -> dict:
    """Recorta só os campos públicos — senha_hash jamais sai do backend."""
    return {
        "usuario_id": registro.get("usuario_id"),
        "nome": registro.get("nome"),
        "email": registro.get("email"),
    }


@router.post("/cadastro", status_code=201)
def criar_conta(dados: CadastroRequest):
    try:
        usuario = usuarios.criar_usuario(dados.nome, dados.email, dados.senha)
    except usuarios.EmailDuplicadoError:
        raise HTTPException(
            status_code=409,
            detail="Este e-mail já tem uma conta. Faça login ou use outro e-mail.",
        )
    except Exception as exc:  # .env sem credenciais, tabela ausente, rede...
        raise _erro_503(exc)

    logger.info("Novo cadastro: %s", usuario["email"])
    return {"usuario": usuario, "mensagem": "Conta criada com sucesso."}


@router.post("/login")
def entrar(dados: LoginRequest):
    try:
        registro = usuarios.buscar_por_email(dados.email)
    except Exception as exc:
        raise _erro_503(exc)

    # Mesma resposta para "e-mail não existe" e "senha errada": enumerar
    # contas existentes por tentativa e erro é uma vulnerabilidade clássica.
    if registro is None or not verificar_senha(dados.senha, registro.get("senha_hash", "")):
        raise HTTPException(
            status_code=401,
            detail="E-mail ou senha incorretos.",
        )

    usuario = _usuario_publico(registro)
    logger.info("Login OK: %s", usuario["email"])
    return {"usuario": usuario, "mensagem": "Login realizado com sucesso."}
