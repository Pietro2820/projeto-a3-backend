"""
Testes das rotas POST /login e POST /cadastro (app/auth/router.py).

O Supabase não pode estar no ar em teste unitário: um client falso
(ClienteFalso) reproduz a cadeia `table().select().eq().limit().execute()`
e `table().insert().execute()` que o app/auth/usuarios.py usa, com um
"banco" em memória. O monkeypatch troca o get_client() do módulo — mesma
estratégia dos outros testes do projeto.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

SENHA_TESTE = "senha12345"


# ---------------------------------------------------------------------------
# Falso client do Supabase — só o que app/auth/usuarios.py usa
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, data):
        self.data = data


class TabelaFalsa:
    """Encadeia select/eq/limit ou insert e executa contra uma lista."""

    def __init__(self, banco):
        self._banco = banco
        self._filtros = {}
        self._insert = None

    # ----- encadeamento de leitura -----
    def select(self, _colunas):
        return self

    def eq(self, campo, valor):
        self._filtros[campo] = valor
        return self

    def limit(self, _n):
        return self

    # ----- encadeamento de escrita -----
    def insert(self, payload):
        self._insert = payload
        return self

    # ----- execução -----
    def execute(self):
        if self._insert is not None:
            return self._executar_insert()

        achados = [
            linha for linha in self._banco
            if all(linha.get(c) == v for c, v in self._filtros.items())
        ]
        return FakeResponse(achados)

    def _executar_insert(self):
        dados = self._insert
        for linha in self._banco:
            if linha["email"] == dados["email"]:
                raise Exception(
                    'duplicate key value violates unique constraint '
                    '"usuarios_email_key"'
                )
        registro = {
            "usuario_id": str(uuid.uuid4()),
            "nome": dados["nome"],
            "email": dados["email"],
            "senha_hash": dados["senha_hash"],
            "criado_em": "2026-01-01T00:00:00+00:00",
        }
        self._banco.append(registro)
        return FakeResponse([registro])


class ClienteFalso:
    def __init__(self, banco):
        self._banco = banco

    def table(self, _nome):
        return TabelaFalsa(self._banco)


@pytest.fixture()
def banco_falso(monkeypatch):
    """Supabase em memória; devolve a lista para os testes inspecionarem."""
    banco = []
    monkeypatch.setattr(
        "app.auth.usuarios.get_client", lambda: ClienteFalso(banco)
    )
    return banco


@pytest.fixture()
def banco_sem_config(monkeypatch):
    """Cenário .env sem SUPABASE_URL/KEY: get_client levanta RuntimeError."""
    def raise_runtime():
        raise RuntimeError("SUPABASE_URL e/ou SUPABASE_KEY não estão definidas")

    monkeypatch.setattr("app.auth.usuarios.get_client", raise_runtime)


# ---------------------------------------------------------------------------
# POST /cadastro
# ---------------------------------------------------------------------------

def test_cadastro_cria_conta_e_devolve_usuario_publico(banco_falso):
    resposta = client.post(
        "/cadastro",
        json={"nome": "Maria Teste", "email": "maria@exemplo.com", "senha": SENHA_TESTE},
    )

    assert resposta.status_code == 201
    usuario = resposta.json()["usuario"]
    assert usuario["nome"] == "Maria Teste"
    assert usuario["email"] == "maria@exemplo.com"
    assert "usuario_id" in usuario
    # senha_hash jamais sai na resposta (nem em nenhum campo do corpo)
    assert "senha_hash" not in usuario
    assert "senha" not in resposta.text


def test_cadastro_guarda_hash_pbkdf2_nao_a_senha(banco_falso):
    client.post(
        "/cadastro",
        json={"nome": "Maria Teste", "email": "maria2@exemplo.com", "senha": SENHA_TESTE},
    )

    salvo = banco_falso[0]
    assert salvo["senha_hash"].startswith("pbkdf2_sha256$")
    assert SENHA_TESTE not in salvo["senha_hash"]


def test_cadastro_email_duplicado_retorna_409(banco_falso):
    corpo = {"nome": "Maria Teste", "email": "dup@exemplo.com", "senha": SENHA_TESTE}
    client.post("/cadastro", json=corpo)

    resposta = client.post("/cadastro", json=corpo)

    assert resposta.status_code == 409
    assert "já tem uma conta" in resposta.json()["detail"]


def test_cadastro_com_email_em_maiusculas_normaliza(banco_falso):
    resposta = client.post(
        "/cadastro",
        json={"nome": "Maria Teste", "email": "  MAIUSCULA@Exemplo.COM  ", "senha": SENHA_TESTE},
    )

    assert resposta.status_code == 201
    assert resposta.json()["usuario"]["email"] == "maiuscula@exemplo.com"


@pytest.mark.parametrize(
    "corpo",
    [
        {"nome": "A", "email": "val@exemplo.com", "senha": SENHA_TESTE},   # nome curto
        {"nome": "Maria", "email": "sem-arroba", "senha": SENHA_TESTE},    # e-mail inválido
        {"nome": "Maria", "email": "val@exemplo.com", "senha": "curta"},   # senha < 8
        {"nome": "Maria", "email": "val@exemplo.com"},                     # senha ausente
        {"email": "val@exemplo.com", "senha": SENHA_TESTE},                # nome ausente
    ],
)
def test_cadastro_com_payload_invalido_retorna_422(corpo):
    resposta = client.post("/cadastro", json=corpo)

    assert resposta.status_code == 422


# ---------------------------------------------------------------------------
# POST /login
# ---------------------------------------------------------------------------

def _cadastrar(banco, email="login@exemplo.com", senha=SENHA_TESTE):
    resposta = client.post(
        "/cadastro", json={"nome": "Maria Login", "email": email, "senha": senha}
    )
    assert resposta.status_code == 201
    return resposta


def test_login_com_credenciais_corretas_retorna_200(banco_falso):
    _cadastrar(banco_falso)

    resposta = client.post(
        "/login", json={"email": "login@exemplo.com", "senha": SENHA_TESTE}
    )

    assert resposta.status_code == 200
    usuario = resposta.json()["usuario"]
    assert usuario["email"] == "login@exemplo.com"
    assert usuario["nome"] == "Maria Login"
    assert "senha_hash" not in usuario


def test_login_com_senha_errada_retorna_401(banco_falso):
    _cadastrar(banco_falso)

    resposta = client.post(
        "/login", json={"email": "login@exemplo.com", "senha": "SenhaErrada99"}
    )

    assert resposta.status_code == 401


def test_login_com_email_inexistente_retorna_401_com_mesma_mensagem(banco_falso):
    """Não revelar quais e-mails têm conta: mesma mensagem nos dois erros."""
    _cadastrar(banco_falso)

    senha_errada = client.post(
        "/login", json={"email": "login@exemplo.com", "senha": "SenhaErrada99"}
    ).json()["detail"]
    email_inexistente = client.post(
        "/login", json={"email": "nao-existe@exemplo.com", "senha": SENHA_TESTE}
    ).json()["detail"]

    assert email_inexistente == senha_errada


def test_login_ignora_maiusculas_do_email(banco_falso):
    _cadastrar(banco_falso, email="CaSo@Exemplo.com")

    resposta = client.post(
        "/login", json={"email": "caso@exemplo.com", "senha": SENHA_TESTE}
    )

    assert resposta.status_code == 200


@pytest.mark.parametrize(
    "corpo",
    [
        {"email": "login@exemplo.com", "senha": "curta"},  # senha < 8
        {"email": "sem-arroba", "senha": SENHA_TESTE},     # e-mail inválido
        {"senha": SENHA_TESTE},                            # e-mail ausente
        {"email": "login@exemplo.com"},                    # senha ausente
    ],
)
def test_login_com_payload_invalido_retorna_422(corpo):
    resposta = client.post("/login", json=corpo)

    assert resposta.status_code == 422


# ---------------------------------------------------------------------------
# Banco indisponível / mal configurado
# ---------------------------------------------------------------------------

def test_login_sem_supabase_configurado_retorna_503(banco_sem_config):
    resposta = client.post(
        "/login", json={"email": "qualquer@exemplo.com", "senha": SENHA_TESTE}
    )

    assert resposta.status_code == 503
    assert "schema_usuarios.sql" in resposta.json()["detail"]


def test_cadastro_sem_supabase_configurado_retorna_503(banco_sem_config):
    resposta = client.post(
        "/cadastro",
        json={"nome": "Maria", "email": "maria@exemplo.com", "senha": SENHA_TESTE},
    )

    assert resposta.status_code == 503
    assert "SUPABASE_URL" in resposta.json()["detail"]
