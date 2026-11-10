"""
Acesso à tabela `usuarios` — autenticação SOMENTE via Supabase.

Diferente dos vídeos (que têm fallback em JSON local para rodar sem rede),
usuário não tem fallback: senha hasheada mora em banco de verdade. Sem
SUPABASE_URL/KEY no .env as rotas de login/cadastro respondem 503 com
mensagem clara, e o front mostra o aviso — não existe "usuário de mentira".

Pré-requisito: rodar `supabase/schema_usuarios.sql` (tabela `usuarios`) e
`supabase/migracao_002_papeis_streak.sql` (colunas role/deve_trocar_senha)
no SQL Editor do Supabase.

PAPEIS (migração 002): 'admin' > 'professor' > 'estudante'. A fonte de
verdade do papel é ESTA camada, não o cliente:

  * criar_usuario   — cadastro PÚBLICO grava 'estudante' FIXO. O payload
    da requisição nem chega a ter campo de papel: não existe forma de um
    cadastro público nascer professor/admin (injeção de papel é o ataque
    clássico que esta decisão elimina);
  * criar_professor — usado só por POST /admin/professores (guard de admin
    em app/auth/papeis.py), nasce com deve_trocar_senha=true;
  * trocar_senha    — atualiza o hash e DESLIGA a flag de 1º acesso.
"""
from __future__ import annotations

import logging

from app.auth.senhas import gerar_hash
from app.database.client import get_client

logger = logging.getLogger(__name__)

TABLE = "usuarios"

# Papéis válidos — espelham o CHECK `usuarios_role_valido` da migração 002.
ADMIN = "admin"
PROFESSOR = "professor"
ESTUDANTE = "estudante"

# Colunas que saem do banco — a senha_hash NUNCA sai daqui para o cliente.
COLUNAS_PUBLICAS = "usuario_id,nome,email,criado_em,role,deve_trocar_senha"


def usuario_publico(registro: dict) -> dict:
    """Recorte ÚNICO de usuário exposto pela API (login, sessão, cadastro,
    troca de senha e admin) — quem usa é app/auth/router.py e app/admin/
    router.py; mantenha tudo por aqui.

    Regras:
      * senha_hash NUNCA entra (nem a palavra "senha" em nome de campo — o
        teste de contrato vigia que ela não aparece em corpo de resposta);
      * a flag de 1º acesso (coluna deve_trocar_senha) sai como
        `primeiro_acesso` — mesmo significado, nome seguro;
      * registros sem role (linha antiga antes da migração 002) contam como
        estudante — o default do banco é este.
    """
    return {
        "usuario_id": registro.get("usuario_id"),
        "nome": registro.get("nome"),
        "email": registro.get("email"),
        "role": registro.get("role", ESTUDANTE),
        "primeiro_acesso": bool(registro.get("deve_trocar_senha", False)),
    }


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


def buscar_por_id(usuario_id: str) -> dict | None:
    """Procura o usuário pela chave primária (usado pelo GET /sessao)."""
    client = _cliente()
    response = (
        client.table(TABLE)
        .select("*")
        .eq("usuario_id", usuario_id)
        .limit(1)
        .execute()
    )
    return response.data[0] if response.data else None


def criar_usuario(nome: str, email: str, senha: str) -> dict:
    """Insere um usuário novo com a senha hasheada (PBKDF2).

    Papel FIXO 'estudante' — gravado aqui no servidor, ignorando
    completamente o que veio na requisição (o router nem repassa).

    Levanta EmailDuplicadoError quando o e-mail já existe (unique no banco).
    A resposta nunca inclui o hash — só os campos públicos.
    """
    return _inserir({
        "nome": nome.strip(),
        "email": email.strip().lower(),
        "senha_hash": gerar_hash(senha),
        "role": ESTUDANTE,
        "deve_trocar_senha": False,
    })


def criar_professor(nome: str, email: str, senha_temporaria: str) -> dict:
    """Insere uma conta de PROFESSOR — só o admin chama (rota /admin).

    Nasce com deve_trocar_senha=True: o 1º acesso é obrigado a trocar a
    senha temporária (POST /trocar-senha) antes de usar qualquer rota
    (guard `autenticado` em app/auth/papeis.py).
    """
    return _inserir({
        "nome": nome.strip(),
        "email": email.strip().lower(),
        "senha_hash": gerar_hash(senha_temporaria),
        "role": PROFESSOR,
        "deve_trocar_senha": True,
    })


def _inserir(payload: dict) -> dict:
    """Insert compartilhado — duplicidade de e-mail vira EmailDuplicadoError."""
    client = _cliente()
    try:
        response = client.table(TABLE).insert(payload).execute()
    except Exception as exc:  # postgrest não tem exceção estável entre versões
        mensagem = str(exc).lower()
        if "duplicate key" in mensagem or "unique" in mensagem or "23505" in mensagem:
            raise EmailDuplicadoError(payload["email"]) from exc
        raise

    return usuario_publico(response.data[0])


def trocar_senha(usuario_id: str, nova_senha: str) -> dict | None:
    """Grava o hash da senha nova e DESLIGA o bloqueio de 1º acesso.

    É o que libera a conta criada pelo admin (deve_trocar_senha=True) —
    a flag sai do banco na hora, e o próximo request pelo cookie já passa
    pelo guard `autenticado`.
    """
    client = _cliente()
    response = (
        client.table(TABLE)
        .update({
            "senha_hash": gerar_hash(nova_senha),
            "deve_trocar_senha": False,
        })
        .eq("usuario_id", usuario_id)
        .execute()
    )
    # update em usuário inexistente volta vazio: None em vez de IndexError.
    return response.data[0] if response.data else None


def listar_todos() -> list[dict]:
    """Lista os usuários para o painel do admin — SEM senha_hash.

    A projeção explícita garante que o hash não vaza nem por descuido de
    quem chama: ele nunca entra na resposta.
    """
    client = _cliente()
    response = client.table(TABLE).select(COLUNAS_PUBLICAS).execute()
    return response.data
