"""
Dependências de AUTORIZAÇÃO por papel — app/auth/papeis.py.

Os papéis são três, com hierarquia crescente:

    estudante  <  professor  <  admin

Como funciona: o token OPACO no cookie HttpOnly identifica a SESSÃO
(app/auth/sessoes.py); estas dependências resolvem quem é e decidem se o
papel pode passar. Elas NUNCA confiam em campo do corpo da requisição —
papel é dado do BANCO, lido a cada request (revogar um papel vale na
hora, sem esperar a sessão expirar).

Bloqueio de 1º acesso: a conta criada pelo admin nasce com
`deve_trocar_senha = true`. Enquanto a flag estiver de pé, passar por
`autenticado` responde 403 pedindo a troca — as únicas rotas que uma
conta nesse estado consegue usar são POST /trocar-senha (usa
`registro_atual`, sem o bloqueio) e POST /logout (não passa por aqui).
O front também redireciona, mas a regra de verdade é esta — front é só
conveniência; curl é o atacante real.

Regras de código HTTP (mesmas do restante da API):
  401 — sem sessão (cookie ausente, inválido ou expirado);
  403 — com sessão, mas sem permissão (papel errado OU 1º acesso pendente).
"""
from __future__ import annotations

from fastapi import HTTPException, Request

from app.auth import sessoes, usuarios


async def registro_atual(request: Request) -> dict:
    """Registro COMPLETO do usuário da sessão (com role e senha_hash).

    Quem usa é o /trocar-senha (precisa verificar a senha atual) e as
    guardas abaixo. Este registro NUNCA sai da API — senha_hash é segredo
    do servidor (ver _usuario_publico no router de auth).
    """
    token = request.cookies.get(sessoes.CHAVE_COOKIE)
    usuario_id = sessoes.resolver_sessao(token)

    if not usuario_id:
        raise HTTPException(
            status_code=401, detail="Sessão inexistente ou expirada."
        )

    try:
        registro = usuarios.buscar_por_id(usuario_id)
    except Exception as exc:
        # .env sem credenciais, tabela/migração ausente, rede fora...
        raise HTTPException(
            status_code=503,
            detail=(
                "Banco de dados indisponível. Verifique SUPABASE_URL/"
                "SUPABASE_KEY no .env e rode supabase/schema_usuarios.sql + "
                "supabase/migracao_002_papeis_streak.sql no SQL Editor."
            ),
        ) from exc

    if registro is None:  # sessão válida apontando para conta apagada
        sessoes.encerrar_sessao(token)
        raise HTTPException(
            status_code=401, detail="Sessão inexistente ou expirada."
        )

    return registro


def _bloquear_primeiro_acesso(registro: dict) -> dict:
    """A conta recém-criada pelo admin só passa depois de trocar a senha."""
    if registro.get("deve_trocar_senha"):
        raise HTTPException(
            status_code=403,
            detail=(
                "Primeiro acesso: troque a senha antes de continuar "
                "(POST /trocar-senha)."
            ),
        )
    return registro


async def autenticado(request: Request) -> dict:
    """Sessão válida + 1º acesso concluído → registro do usuário."""
    registro = await registro_atual(request)
    return _bloquear_primeiro_acesso(registro)


async def professor_ou_admin(request: Request) -> dict:
    """Guard de UPLOAD/EDIÇÃO de vídeo: professor ou admin (o admin herda
    os poderes de professor — ele gerencia a plataforma inteira)."""
    registro = await autenticado(request)
    if registro.get("role") not in ("professor", "admin"):
        raise HTTPException(
            status_code=403,
            detail="Apenas professores podem fazer isso.",
        )
    return registro


async def admin(request: Request) -> dict:
    """Guard de ADMINISTRAÇÃO: só o papel admin passa."""
    registro = await autenticado(request)
    if registro.get("role") != "admin":
        raise HTTPException(
            status_code=403,
            detail="Apenas administradores podem fazer isso.",
        )
    return registro


async def estudante(request: Request) -> dict:
    """Guard do STREAK: recurso exclusivo do estudante.

    Privacidade combinada: professor/admin NÃO têm sequência de estudos —
    nem a deles nem a de ninguém (o professor não vigia o aluno).
    """
    registro = await autenticado(request)
    if registro.get("role") != "estudante":
        raise HTTPException(
            status_code=403,
            detail="A sequência de estudos é um recurso para estudantes.",
        )
    return registro
