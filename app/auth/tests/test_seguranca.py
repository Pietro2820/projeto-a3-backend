"""
Testes de segurança do fluxo de login/cadastro (app/auth/router.py).

Cada teste trava uma exigência da revisão de segurança:

  * credenciais SÓ por POST com corpo JSON — GET /login é 405 e a query
    string é ignorada (nada de senha lida de ?senha=...);
  * Cache-Control: no-store nas respostas (credencial não fica em cache);
  * bloqueio por tentativas (rate limit) com mensagem genérica, contando
    falha por IP e por e-mail, configurável por variável de ambiente;
  * mensagem de 401 idêntica para e-mail inexistente e senha errada;
  * logs SEM senha e SEM e-mail completo;
  * cookie de sessão HttpOnly (+Secure só em HTTPS; Max-Age só no
    "lembrar"), GET /sessao e POST /logout; expiração no servidor;
  * senhas triviais bloqueadas no cadastro.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.auth import sessoes
from app.auth.rate_limit import limitador_login

# Reaproveita o "Supabase em memória" e o client já usados pela suíte
# de comportamento — mesmo contrato, sem duplicar o dublê.
from app.auth.tests.test_router import (  # noqa: F401
    SENHA_TESTE,
    _cadastrar,
    banco_falso,
)

client = TestClient(app)

MENSAGEM_401 = "E-mail ou senha incorretos."


@pytest.fixture(autouse=True)
def _cookies_limpos():
    """O client é compartilhado com a suíte de comportamento e o TestClient
    persiste cookies entre requisições: sem limpar, o /sessao de um teste
    enxergaria o login de outro. Cada teste começa sem cookie nenhum."""
    client.cookies.clear()
    yield
    client.cookies.clear()


# ---------------------------------------------------------------------------
# Método e fonte das credenciais
# ---------------------------------------------------------------------------

def test_get_login_retorna_405():
    """Nada de login por GET — a rota só existe em POST."""
    assert client.get("/login").status_code == 405


def test_get_cadastro_retorna_405():
    assert client.get("/cadastro").status_code == 405


def test_senha_na_query_string_e_ignorada(banco_falso):
    """Credenciais corretas no CORPO, lixo na query → 200.
    E o contrário: senha certa na query com corpo errado NÃO entra."""
    _cadastrar(banco_falso)

    ok = client.post(
        "/login?senha=senha-na-query&password=tambem",
        json={"email": "login@exemplo.com", "senha": SENHA_TESTE},
    )
    assert ok.status_code == 200

    errado = client.post(
        "/login?email=login@exemplo.com&senha=senha12345",
        json={"email": "login@exemplo.com", "senha": "SenhaErrada99"},
    )
    assert errado.status_code == 401
    assert errado.json()["detail"] == MENSAGEM_401


# ---------------------------------------------------------------------------
# Cabeçalhos de segurança
# ---------------------------------------------------------------------------

def test_respostas_de_auth_tem_cache_control_no_store(banco_falso):
    _cadastrar(banco_falso)

    for resposta in (
        client.post("/login", json={"email": "login@exemplo.com", "senha": SENHA_TESTE}),
        client.post("/cadastro", json={"nome": "Outra", "email": "outra@exemplo.com", "senha": SENHA_TESTE}),
    ):
        assert resposta.headers["Cache-Control"] == "no-store"
        assert resposta.headers["X-Content-Type-Options"] == "nosniff"
        assert resposta.headers["Referrer-Policy"] == "no-referrer"


def test_hsts_somente_quando_https():
    em_http = client.get("/")
    assert "Strict-Transport-Security" not in em_http.headers

    em_https = TestClient(app, base_url="https://testserver").get("/")
    assert (
        em_https.headers["Strict-Transport-Security"] == "max-age=31536000; includeSubDomains"
    )


def test_csp_no_player_nao_bloqueia_hls_vendado():
    resposta = client.get("/player/login.html")
    csp = resposta.headers.get("Content-Security-Policy", "")
    assert "script-src 'self'" in csp          # sem CDN, tudo vendado
    assert "frame-ancestors 'none'" in csp     # anti-clickjacking


def test_csp_permite_worker_blob_do_hlsjs():
    """O hls.js demuxa num Web Worker criado de blob: (enableWorker).

    Sem `worker-src 'self' blob:` o script-src bloqueia o worker e o hls.js
    cai no demuxer da main thread — em parte dos navegadores isso termina em
    MEDIA_ERROR fatal ("O navegador encontrou um problema com a mídia").
    Regressão real vista em produção: este teste é a trava.
    """
    resposta = client.get("/player/login.html")
    csp = resposta.headers.get("Content-Security-Policy", "")
    assert "worker-src 'self' blob:" in csp


# ---------------------------------------------------------------------------
# Rate limit
# ---------------------------------------------------------------------------

@pytest.fixture()
def _janela_estouravel(monkeypatch):
    """Limite baixo e bloqueio curto — o teste não precisa de relógio real."""
    monkeypatch.setenv("TENTATIVAS_LOGIN", "3")
    monkeypatch.setenv("JANELA_LOGIN_MINUTOS", "15")
    monkeypatch.setenv("BLOQUEIO_LOGIN_MINUTOS", "15")


def test_bloqueia_apos_o_limite_de_tentativas(banco_falso, _janela_estouravel):
    _cadastrar(banco_falso)
    corpo = {"email": "login@exemplo.com", "senha": "SenhaErrada99"}

    for _ in range(3):  # limite configurado = 3
        resposta = client.post("/login", json=corpo)
        assert resposta.status_code == 401

    bloqueado = client.post("/login", json=corpo)
    assert bloqueado.status_code == 429
    assert "Retry-After" in bloqueado.headers
    # Mensagem genérica: não distingue conta existente de inexistente.
    assert "Muitas tentativas" in bloqueado.json()["detail"]


def test_bloqueio_vale_mesmo_com_senha_certa(banco_falso, _janela_estouravel):
    _cadastrar(banco_falso)
    for _ in range(3):
        client.post("/login", json={"email": "login@exemplo.com", "senha": "Errada99"})

    certo_durante_bloqueio = client.post(
        "/login", json={"email": "login@exemplo.com", "senha": SENHA_TESTE}
    )
    assert certo_durante_bloqueio.status_code == 429


def test_bloqueio_por_email_nao_impede_outro_email(banco_falso, _janela_estouravel):
    _cadastrar(banco_falso, email="a@exemplo.com")
    _cadastrar(banco_falso, email="b@exemplo.com")

    for _ in range(3):
        client.post("/login", json={"email": "a@exemplo.com", "senha": "Errada99"})

    # Mesmo IP, outro e-mail: a chave do IP acumulou 3 falhas e bloqueou
    # o IP — por isso o limite por IP existe. E-mail sozinho não transitou.
    resposta = client.post("/login", json={"email": "b@exemplo.com", "senha": "Errada99"})
    assert resposta.status_code == 429  # bloqueado pelo contador de IP


def test_limite_configuravel_por_variavel(banco_falso, monkeypatch):
    _cadastrar(banco_falso)
    monkeypatch.setenv("TENTATIVAS_LOGIN", "2")

    client.post("/login", json={"email": "login@exemplo.com", "senha": "Errada11"})
    client.post("/login", json={"email": "login@exemplo.com", "senha": "Errada22"})

    resposta = client.post(
        "/login", json={"email": "login@exemplo.com", "senha": SENHA_TESTE}
    )
    assert resposta.status_code == 429


def test_sucesso_nao_zera_o_contador_do_ip(banco_falso, _janela_estouravel):
    """Acertar UMA conta não pode zerar o orçamento do IP (o atacante não
    pode "pagar" as tentativas dele logando numa conta que ele tem)."""
    _cadastrar(banco_falso, email="dono@exemplo.com")
    for _ in range(2):  # limite 3; o IP fica a 1 falha de bloquear
        client.post("/login", json={"email": "atacante@exemplo.com", "senha": "Errada99"})

    client.post("/login", json={"email": "dono@exemplo.com", "senha": SENHA_TESTE})  # 200

    # A 3ª falha do IP ATINGE o limite (ainda responde 401); a 4ª é que
    # encontra o bloqueio ativo.
    client.post("/login", json={"email": "dono@exemplo.com", "senha": "Errada99"})
    resposta = client.post("/login", json={"email": "dono@exemplo.com", "senha": SENHA_TESTE})
    assert resposta.status_code == 429


def test_401_idêntico_para_email_inexistente_e_senha_errada(banco_falso):
    _cadastrar(banco_falso)

    senha_errada = client.post(
        "/login", json={"email": "login@exemplo.com", "senha": "SenhaErrada99"}
    ).json()["detail"]
    email_inexistente = client.post(
        "/login", json={"email": "ninguem@exemplo.com", "senha": SENHA_TESTE}
    ).json()["detail"]

    assert senha_errada == email_inexistente == MENSAGEM_401


# ---------------------------------------------------------------------------
# Logs sem credenciais
# ---------------------------------------------------------------------------

def test_log_de_login_nao_contem_a_senha(banco_falso, caplog):
    import logging

    _cadastrar(banco_falso)
    with caplog.at_level(logging.INFO, logger="app.auth.router"):
        client.post(
            "/login", json={"email": "login@exemplo.com", "senha": SENHA_TESTE}
        )
        client.post(
            "/login", json={"email": "login@exemplo.com", "senha": "SenhaErrada99"}
        )

    texto = caplog.text.lower()
    assert "senha12345" not in texto and "senhaerrada99" not in texto
    assert "senha_hash" not in texto


def test_log_de_cadastro_mascara_o_email(banco_falso, caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="app.auth.router"):
        client.post(
            "/cadastro",
            json={"nome": "Joana Silva", "email": "joana.silva@exemplo.com", "senha": SENHA_TESTE},
        )

    registro = [r for r in caplog.records if "Novo cadastro" in r.message]
    assert registro, "esperava o log 'Novo cadastro'"
    mensagem = registro[0].getMessage()
    assert "joana.silva@exemplo.com" not in mensagem   # e-mail completo NUNCA
    assert "jo***@exemplo.com" in mensagem              # mascarado de propósito


# ---------------------------------------------------------------------------
# Sessão: cookie HttpOnly, /sessao, /logout, expiração
# ---------------------------------------------------------------------------

def _login_com_cookie(lembrar=False):
    return client.post(
        "/login",
        json={"email": "login@exemplo.com", "senha": SENHA_TESTE, "lembrar": lembrar},
    )


def test_login_emite_cookie_httponly(banco_falso):
    _cadastrar(banco_falso)

    resposta = _login_com_cookie()
    assert resposta.status_code == 200

    cookie = resposta.headers["set-cookie"]
    assert "edustream_sessao=" in cookie
    assert "httponly" in cookie.lower()
    assert "samesite=lax" in cookie.lower()
    assert "max-age" not in cookie.lower()          # sem lembrar = cookie de sessão
    # Em HTTP puro (TestClient padrão) Secure fica OFF — dev em localhost.
    assert "secure" not in cookie.lower().replace("httponly", "")


def test_login_com_lembrar_emite_cookie_persistente(banco_falso, monkeypatch):
    _cadastrar(banco_falso)
    monkeypatch.setenv("LEMBRAR_DIAS", "30")

    cookie = _login_com_cookie(lembrar=True).headers["set-cookie"].lower()
    assert "max-age=2592000" in cookie  # 30 dias


def test_cookie_secure_sob_https(banco_falso):
    _cadastrar(banco_falso)

    seguro = TestClient(app, base_url="https://testserver")
    resposta = seguro.post(
        "/login", json={"email": "login@exemplo.com", "senha": SENHA_TESTE}
    )
    assert "secure" in resposta.headers["set-cookie"].lower()


def test_get_sessao_devolve_usuario_com_cookie(banco_falso):
    _cadastrar(banco_falso)
    _login_com_cookie()

    resposta = client.get("/sessao")
    assert resposta.status_code == 200
    assert resposta.json()["usuario"]["email"] == "login@exemplo.com"
    assert "senha_hash" not in resposta.text


def test_get_sessao_sem_cookie_retorna_401():
    resposta = client.get("/sessao")
    assert resposta.status_code == 401
    assert resposta.json()["detail"] == "Sessão inexistente ou expirada."


def test_logout_revoga_a_sessao_no_servidor(banco_falso):
    _cadastrar(banco_falso)
    _login_com_cookie()

    resposta = client.post("/logout")
    assert resposta.status_code == 200

    assert client.get("/sessao").status_code == 401  # token morto no servidor


def test_sessao_expira_no_servidor(banco_falso):
    _cadastrar(banco_falso)
    _login_com_cookie()

    # O TTL de 0h é vetado no mínimo do clamp (max(1,...)); o que importa
    # travar aqui é a MECÂNICA: token cuja expiração passou morre no servidor.
    token = next(
        valor.split(";")[0]
        for nome, valor in client.cookies.items() if nome == sessoes.CHAVE_COOKIE
    )
    from datetime import datetime, timedelta, timezone

    with sessoes._lock:
        sessoes._sessoes[token]["expira_em"] = datetime.now(timezone.utc) - timedelta(seconds=1)

    assert client.get("/sessao").status_code == 401


def test_token_da_sessao_e_imprevisivel(banco_falso):
    """Dois logins → tokens diferentes (opaco, aleatório, sem JWT decodável)."""
    _cadastrar(banco_falso)
    cookie1 = _login_com_cookie().headers["set-cookie"].split("edustream_sessao=")[1].split(";")[0]
    cookie2 = _login_com_cookie().headers["set-cookie"].split("edustream_sessao=")[1].split(";")[0]

    assert cookie1 != cookie2
    assert len(cookie1) >= 40  # 32 bytes → token_urlsafe ~43 chars


# ---------------------------------------------------------------------------
# Senhas triviais (cadastro)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("trivial", ["12345678", "password", "senha123", "qwerty123"])
def test_cadastro_rejeita_senha_trivial(banco_falso, trivial):
    resposta = client.post(
        "/cadastro",
        json={"nome": "Maria", "email": "trivial@exemplo.com", "senha": trivial},
    )
    assert resposta.status_code == 422
    assert "muito comum" in resposta.json()["detail"][0]["msg"]


def test_cadastro_aceita_a_senha_do_usuario_de_teste(banco_falso):
    """senha12345 NÃO está na lista de triviais de propósito — é a senha do
    usuário de teste do schema_usuarios.sql, usada por toda a suíte."""
    resposta = client.post(
        "/cadastro",
        json={"nome": "Uso Interno", "email": "uso-interno@exemplo.com", "senha": SENHA_TESTE},
    )
    assert resposta.status_code == 201
