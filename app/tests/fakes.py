"""
Client FALSO do Supabase compartilhado pelos testes novos (papéis, admin,
propriedade de vídeo, streak) — app/tests/fakes.py.

O supabase-py é um cliente REST: tudo que o backend usa são cadeias

    client.table("t").select(...).eq(...).limit(...).execute()   # leitura
    client.table("t").insert(payload).execute()                  # escrita
    client.table("t").update(payload).eq(...).execute()          # update
    client.table("t").upsert(payload, on_conflict=...,
                             ignore_duplicates=True).execute()   # idempotente

Este fake reproduz EXATAMENTE essas cadeias contra listas em memória — o
mesmo padrão do TabelaFalsa de app/auth/tests/test_router.py, estendido com
update/upsert e com as três tabelas da migração 002 (usuarios, videos,
dias_estudo). Nada de rede: os testes de autorização exercem as guardas reais
sem depender de serviço externo.

Helpers de conta: inserir_usuario cria a linha direto no "banco" (com hash
PBKDF2 de verdade) e logar_com client → POST /login → o TestClient guarda o
cookie de sessão para os próximos requests — é o fluxo real do login, sem
dependência de cadastro público.
"""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.auth.senhas import gerar_hash
from app.main import app

SENHA_PADRAO = "senha12345"


class FakeResponse:
    def __init__(self, data):
        self.data = data


def _linhas(payload) -> list[dict]:
    """Supabase aceita um dict ou uma lista de dicts no insert/upsert."""
    return payload if isinstance(payload, list) else [payload]


class TabelaFalsa:
    """Encadeia select/eq/limit, insert, update e upsert contra uma lista."""

    def __init__(self, banco: list[dict], tabela: str):
        self._banco = banco
        self._tabela = tabela
        self._filtros: dict = {}
        self._limite: int | None = None
        self._colunas = "*"
        self._payload = None
        self._modo = "select"
        self._ignore_duplicates = False
        self._chaves_upsert: list[str] = []

    # ----- encadeamento de leitura -----
    def select(self, colunas="*"):
        # Projeção HONESTA: o select("a,b") do banco real só devolve a,b —
        # é o que impede o senha_hash de aparecer na lista do admin.
        self._colunas = colunas or "*"
        return self

    def eq(self, campo, valor):
        self._filtros[campo] = valor
        return self

    def limit(self, n):
        self._limite = n
        return self

    # ----- encadeamento de escrita -----
    def insert(self, payload):
        self._payload = payload
        self._modo = "insert"
        return self

    def update(self, payload):
        self._payload = payload
        self._modo = "update"
        return self

    def upsert(self, payload, on_conflict=None, ignore_duplicates=False):
        self._payload = payload
        self._modo = "upsert"
        self._ignore_duplicates = ignore_duplicates
        self._chaves_upsert = [
            chave.strip() for chave in (on_conflict or "").split(",") if chave.strip()
        ]
        return self

    def delete(self):
        self._modo = "delete"
        return self

    # ----- execução -----
    def execute(self):
        if self._modo == "insert":
            return self._executar_insert()
        if self._modo == "update":
            return self._executar_update()
        if self._modo == "upsert":
            return self._executar_upsert()
        if self._modo == "delete":
            return self._executar_delete()
        return self._executar_select()

    def _filtrar(self):
        return [
            linha for linha in self._banco
            if all(linha.get(campo) == valor for campo, valor in self._filtros.items())
        ]

    def _executar_select(self):
        achados = self._filtrar()
        if self._limite is not None:
            achados = achados[: self._limite]
        if self._colunas != "*":
            pedidas = [c.strip() for c in self._colunas.split(",") if c.strip()]
            achados = [
                {c: linha[c] for c in pedidas if c in linha} for linha in achados
            ]
        return FakeResponse(achados)

    def _default_de_insert(self, linha: dict) -> dict:
        """Campos com default no banco real, preenchidos aqui (migração 002)."""
        registro = dict(linha)
        if self._tabela == "usuarios":
            registro.setdefault("usuario_id", str(uuid.uuid4()))
            registro.setdefault("criado_em", "2026-01-01T00:00:00+00:00")
        elif self._tabela == "videos":
            registro.setdefault("video_id", str(uuid.uuid4()))
            registro.setdefault("status", "pending")
            registro.setdefault("criado_em", "2026-01-01T00:00:00+00:00")
        elif self._tabela == "dias_estudo":
            registro.setdefault("registrado_em", "2026-01-01T00:00:00+00:00")
        return registro

    def _executar_insert(self):
        for linha in _linhas(self._payload):
            registro = self._default_de_insert(linha)
            if self._tabela == "usuarios" and any(
                r.get("email") == registro.get("email") for r in self._banco
            ):
                # Mesmo formato do PostgREST que app/auth/usuarios.py trata.
                raise Exception(
                    'duplicate key value violates unique constraint '
                    '"usuarios_email_unico"'
                )
            self._banco.append(registro)
            inseridos = self._banco[-1:]
        return FakeResponse(list(inseridos))

    def _executar_update(self):
        alteradas = []
        for linha in self._banco:
            if all(linha.get(c) == v for c, v in self._filtros.items()):
                linha.update(self._payload)
                alteradas.append(linha)
        return FakeResponse(list(alteradas))

    def _executar_delete(self):
        apagadas = self._filtrar()
        for linha in apagadas:
            self._banco.remove(linha)
        return FakeResponse(list(apagadas))

    def _executar_upsert(self):
        resultado = []
        for linha in _linhas(self._payload):
            existente = next(
                (
                    r for r in self._banco
                    if all(r.get(c) == linha.get(c) for c in self._chaves_upsert)
                ),
                None,
            )
            if existente is not None:
                if self._ignore_duplicates:
                    resultado.append(existente)
                    continue  # idempotência: a linha que já existe vence
                existente.update(linha)
                resultado.append(existente)
                continue
            registro = self._default_de_insert(linha)
            self._banco.append(registro)
            resultado.append(registro)
        return FakeResponse(list(resultado))


class ClienteFalso:
    def __init__(self, banco_por_tabela: dict[str, list[dict]]):
        self._banco = banco_por_tabela

    def table(self, nome: str) -> TabelaFalsa:
        return TabelaFalsa(self._banco.setdefault(nome, []), nome)


def banco_novo() -> dict[str, list[dict]]:
    """As três tabelas da migração 002, vazias — o teste inspeciona à vontade."""
    return {"usuarios": [], "videos": [], "dias_estudo": []}


def trocar_get_client(monkeypatch, banco: dict, *modulos: str) -> None:
    """Aponta get_client() dos módulos para o fake.

    Cada módulo importa get_client para o próprio namespace
    (`from app.database.client import get_client`), então o monkeypatch
    precisa mirar CADA namespace que o fluxo do teste alcança.
    """
    def fabricar():
        return ClienteFalso(banco)

    for modulo in modulos:
        monkeypatch.setattr(f"{modulo}.get_client", fabricar)


# ---------------------------------------------------------------------------
# Helpers de conta + login (fluxo REAL: POST /login → cookie de sessão)
# ---------------------------------------------------------------------------

def inserir_usuario(
    banco: dict,
    *,
    nome: str,
    email: str,
    senha: str = SENHA_PADRAO,
    role: str = "estudante",
    deve_trocar_senha: bool = False,
) -> dict:
    """Insere a linha do usuário direto no banco falso (com hash de verdade).

    O papel vem para CÁ de propósito: nos testes, quem escreve a tabela é o
    equivalente do admin (no dia a dia: POST /admin/professores ou o seed).
    """
    registro = {
        "usuario_id": str(uuid.uuid4()),
        "nome": nome,
        "email": email.strip().lower(),
        "senha_hash": gerar_hash(senha),
        "role": role,
        "deve_trocar_senha": deve_trocar_senha,
        "criado_em": "2026-01-01T00:00:00+00:00",
    }
    banco["usuarios"].append(registro)
    return registro


def cliente_novo() -> TestClient:
    """TestClient com jar de cookies PRÓPRIO (sessão não vaza entre testes)."""
    return TestClient(app)


def logar(client: TestClient, email: str, senha: str = SENHA_PADRAO) -> dict:
    """POST /login real — o cookie edustream_sessao fica guardado no client."""
    resposta = client.post("/login", json={"email": email, "senha": senha})
    assert resposta.status_code == 200, resposta.text
    return resposta.json()["usuario"]


def usuario_com_sessao(
    banco: dict,
    *,
    role: str,
    nome: str,
    email: str,
    senha: str = SENHA_PADRAO,
    deve_trocar_senha: bool = False,
):
    """Cria o usuário no banco falso e devolve (registro, TestClient logado)."""
    registro = inserir_usuario(
        banco,
        nome=nome,
        email=email,
        senha=senha,
        role=role,
        deve_trocar_senha=deve_trocar_senha,
    )
    client = cliente_novo()
    logar(client, email, senha)
    return registro, client
