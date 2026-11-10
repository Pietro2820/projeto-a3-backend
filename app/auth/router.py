"""
Rotas de autenticação — POST /login, /cadastro, /trocar-senha, /logout, GET /sessao.

A tela de login da branch frontend-css enviava os dados simulados
(setTimeout + redirect); este router é o "TODO(integração)" que o
js/login.js marcava: agora o front chama a API de verdade e o backend
valida as credenciais contra a tabela `usuarios` do Supabase.

Contrato (o que o front espera — não mudar sem atualizar js/login.js):

  POST /cadastro      {nome, email, senha}        -> 201 {usuario:{...}}
                                                  -> 409 e-mail já cadastrado
                                                  -> 422 validação (Pydantic,
                                                     inclusive senha trivial)
                                                  -> 503 banco indisponível
  POST /login         {email, senha, lembrar?}    -> 200 {usuario:{...}} +
                                                     Set-Cookie edustream_sessao
                                                  -> 401 credenciais erradas
                                                  -> 429 bloqueio temporário
                                                  -> 503 banco indisponível
  GET  /sessao        (cookie)                    -> 200 {usuario:{...}}
                                                  -> 401 sessão inexistente
                                                     ou expirada
  POST /trocar-senha  {senha_atual, nova_senha}   -> 200 {usuario:{...}}
                                                     (guard: só a própria
                                                     sessão, SEM o bloqueio
                                                     de 1º acesso — é ele que
                                                     libera a conta nova)
                                                  -> 401 sem sessão
                                                  -> 401 senha_atual errada
                                                  -> 422 senha nova trivial
  POST /logout        (cookie)                    -> 200 revoga o token no
                                                     servidor e limpa o cookie

Todo `usuario` devolvido carrega `role` (admin/professor/estudante) e
`primeiro_acesso` — o front roteia por papel e redireciona a conta nova
para a troca de senha. O papel é lido do BANCO (migração 002); o cadastro
público grava 'estudante' fixo em app/auth/usuarios.py. (No banco a coluna
se chama deve_trocar_senha; na API o campo é primeiro_acesso — o nome é
proposital: o teste de contrato garante que a palavra "senha" não aparece
em NENHUM corpo de resposta.)

Segurança das credenciais: só chegam por POST com corpo JSON — as rotas não
lem query string; GET /login responde 405 (método não permitido). Os logs
nunca registram senha, corpo ou e-mail completo (ver mascarar_email).

Sessão: token OPACO aleatório em cookie HttpOnly (app/auth/sessoes.py) —
invisível ao JavaScript; o guard do front revalida via GET /sessao.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, Response

from app.auth import papeis, sessoes, usuarios
from app.auth.rate_limit import limitador_login
from app.auth.schemas import CadastroRequest, LoginRequest, TrocaSenhaRequest
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
    """Recorte público ÚNICO — implementação em usuarios.usuario_publico.

    `role` e `primeiro_acesso` são públicos de propósito: o front roteia
    por papel (estudante → home, professor → meus vídeos, admin → painel)
    e força o 1º acesso à troca de senha. A AUTORIZAÇÃO, porém, é sempre
    revalidada no servidor (app/auth/papeis.py) — o front é conveniência.
    """
    return usuarios.usuario_publico(registro)


def mascarar_email(email: str) -> str:
    """Disfarça o e-mail para logs: jo***@exemplo.com.

    Os logs não podem registrar o e-mail completo (a exigência vale para
    aplicação, uvicorn e Celery — uvicorn só loga a LINHA de requisição, que
    com POST+JSON não contém credenciais; o corpo nunca é logado).
    """
    email = (email or "").strip()
    if "@" not in email:
        return "***"
    local, dominio = email.split("@", 1)
    return f"{local[:2]}***@{dominio}"


def _eh_https(request: Request) -> bool:
    """O tráfego desta requisição é HTTPS? (direto ou atrás de proxy)"""
    if request.url.scheme == "https":
        return True
    encaminhado = request.headers.get("x-forwarded-proto", "")
    return encaminhado.split(",")[0].strip().lower() == "https"


def _chave_ip(request: Request) -> str:
    # TestClient não tem client — cai no "desconhecido" (uma chave só).
    host = request.client.host if request.client else "desconhecido"
    return f"ip:{host}"


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

    logger.info("Novo cadastro: %s", mascarar_email(usuario["email"]))
    return {"usuario": usuario, "mensagem": "Conta criada com sucesso."}


@router.post("/login")
def entrar(dados: LoginRequest, request: Request, response: Response):
    # Rate limit ANTES de tocar o banco: bloqueado não gera consulta nem
    # revela nada sobre a conta (mensagem genérica, Retry-After honesto).
    chave_email = f"email:{dados.email}"
    chave_ip = _chave_ip(request)
    restante = max(
        limitador_login.bloqueado(chave_email),
        limitador_login.bloqueado(chave_ip),
    )
    if restante > 0:
        raise HTTPException(
            status_code=429,
            detail="Muitas tentativas. Aguarde alguns minutos e tente de novo.",
            headers={"Retry-After": str(int(restante) + 1)},
        )

    try:
        registro = usuarios.buscar_por_email(dados.email)
    except Exception as exc:
        raise _erro_503(exc)

    # Mesma resposta para "e-mail não existe" e "senha errada": enumerar
    # contas existentes por tentativa e erro é uma vulnerabilidade clássica.
    if registro is None or not verificar_senha(dados.senha, registro.get("senha_hash", "")):
        # 503 acima é infraestrutura e NÃO conta como falha de credencial;
        # aqui é falha real — conta para o IP e para o e-mail tentado.
        limitador_login.registrar_falha(chave_email)
        limitador_login.registrar_falha(chave_ip)
        raise HTTPException(
            status_code=401,
            detail="E-mail ou senha incorretos.",
        )

    # Sucesso: limpa o contador do E-MAIL (o do IP permanece — ver
    # LimitadorLogin.resetar) e abre a sessão no cookie HttpOnly.
    limitador_login.resetar(chave_email)
    token, ttl = sessoes.criar_sessao(registro["usuario_id"], lembrar=dados.lembrar)
    response.set_cookie(
        key=sessoes.CHAVE_COOKIE,
        value=token,
        httponly=True,          # JS não lê — token à prova de XSS
        samesite="lax",         # navegação normal envia; site de terceiros não
        secure=_eh_https(request),  # dev em localhost: off; produção HTTPS: on
        max_age=int(ttl.total_seconds()) if dados.lembrar else None,
        path="/",
    )

    usuario = _usuario_publico(registro)
    logger.info("Login OK: %s", mascarar_email(usuario["email"]))
    return {"usuario": usuario, "mensagem": "Login realizado com sucesso."}


@router.get("/sessao")
def sessao_atual(request: Request):
    """O guard do front consulta aqui: cookie HttpOnly → usuário público."""
    token = request.cookies.get(sessoes.CHAVE_COOKIE)
    usuario_id = sessoes.resolver_sessao(token)

    if not usuario_id:
        # Mensagem única: não distingue "nunca logou" de "expirou".
        raise HTTPException(
            status_code=401,
            detail="Sessão inexistente ou expirada.",
        )

    try:
        registro = usuarios.buscar_por_id(usuario_id)
    except Exception as exc:
        raise _erro_503(exc)

    if registro is None:  # sessão válida apontando para conta apagada
        sessoes.encerrar_sessao(token)
        raise HTTPException(status_code=401, detail="Sessão inexistente ou expirada.")

    return {"usuario": _usuario_publico(registro), "mensagem": "Sessão ativa."}


@router.post("/logout")
def sair(request: Request, response: Response):
    """Logout real: revoga o token no servidor e apaga o cookie no cliente.

    Idempotente de propósito (sempre 200): logout não deve falhar por já
    estar deslogado — e não revela se havia sessão.
    """
    token = request.cookies.get(sessoes.CHAVE_COOKIE)
    sessoes.encerrar_sessao(token)
    response.delete_cookie(key=sessoes.CHAVE_COOKIE, path="/")
    return {"mensagem": "Logout realizado."}


@router.post("/trocar-senha")
async def trocar_senha(dados: TrocaSenhaRequest, request: Request):
    """Troca a senha da própria sessão — e libera a conta de 1º acesso.

    É a ÚNICA rota autenticada que aceita uma conta com
    `deve_trocar_senha=true` (usa `registro_atual`, sem o bloqueio — ver
    app/auth/papeis.py): exigir a senha ATUAL garante que é o dono da
    conta quem troca, mesmo com o cookie válido de uma sessão aberta.

    Depois de trocar, a MESMA sessão continua válida (não força relogin)
    e o usuário atualizado volta na resposta com deve_trocar_senha=false.
    """
    registro = await papeis.registro_atual(request)

    if not verificar_senha(dados.senha_atual, registro.get("senha_hash", "")):
        # 401 no campo errado (não 403): a credencial apresentada não bate.
        # Sem rate limit extra — o login já limita tentativas de credencial.
        raise HTTPException(status_code=401, detail="A senha atual não confere.")

    try:
        atualizado = usuarios.trocar_senha(
            registro["usuario_id"], dados.nova_senha
        )
    except Exception as exc:
        raise _erro_503(exc) from exc

    if atualizado is None:  # conta apagada com sessão viva
        sessoes.encerrar_sessao(request.cookies.get(sessoes.CHAVE_COOKIE))
        raise HTTPException(
            status_code=401, detail="Sessão inexistente ou expirada."
        )

    logger.info("Senha trocada: %s", mascarar_email(atualizado["email"]))
    return {
        "usuario": _usuario_publico(atualizado),
        "mensagem": "Senha trocada com sucesso.",
    }
