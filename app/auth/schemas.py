"""
Contratos de entrada das rotas de autenticação (Pydantic).

Espelham as validações da tela de login (js/login.js): e-mail com formato
mínimo `algo@dominio.tld` e senha com no mínimo 8 caracteres. O backend
revalida tudo de novo de propósito — nunca confie no cliente: dá para
chamar a API com curl/Postman burlando o HTML.
"""
from __future__ import annotations

import re

from pydantic import BaseModel, field_validator

# Mesma regex da tela (EMAIL_REGEXP em js/login.js): formato mínimo
# algo@algo.tld — validação real de e-mail só existe com confirmação.
EMAIL_REGEXP = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

SENHA_MINIMA = 8
NOME_MINIMO = 2
TAMANHO_MAXIMO = 120

# Senhas triviais bloqueadas no CADASTRO (defesa contra credenciais fáceis de
# adivinhar — checagem literal, sem biblioteca nova). De propósito a lista NÃO
# contém "senha12345": é a senha do usuário de teste documentada no
# supabase/schema_usuarios.sql e usada por toda a suíte (pytest/Jest/E2E).
SENHAS_TRIVIAIS = frozenset({
    "12345678", "123456789", "1234567890", "12341234", "11223344", "87654321",
    "password", "password1", "password123", "senha123", "senha1234",
    "abc12345", "abcd1234", "qwerty123", "qwertyuiop", "1234qwer",
    "iloveyou", "admin123", "admin1234", "letmein123", "welcome1",
    "monkey123", "dragon123", "football1", "sunshine1", "princess1",
})


def validar_forca_da_senha(valor: str) -> str:
    """Regras compartilhadas de CADASTRO e TROCA DE SENHA.

    Extraído para função livre de propósito: cadastro e troca de senha não
    podem divergir com o tempo (um validator por classe tende a divergir).
    """
    if not valor:
        raise ValueError("Informe sua senha.")
    if len(valor) < SENHA_MINIMA:
        raise ValueError(f"A senha deve ter pelo menos {SENHA_MINIMA} caracteres.")
    if len(valor) > TAMANHO_MAXIMO:
        raise ValueError(f"A senha deve ter no máximo {TAMANHO_MAXIMO} caracteres.")
    if valor.lower() in SENHAS_TRIVIAIS:
        raise ValueError("Esta senha é muito comum — escolha outra menos óbvia.")
    return valor


class LoginRequest(BaseModel):
    email: str
    senha: str
    # "Lembrar de mim": só faz sentido no LOGIN. False (padrão) = cookie de
    # sessão (some ao fechar o navegador); True = cookie com Max-Age
    # (LEMBRAR_DIAS) e token com a mesma validade no servidor. Opcional para
    # clientes antigos/curl não quebrarem.
    lembrar: bool = False

    @field_validator("email")
    @classmethod
    def validar_email(cls, valor: str) -> str:
        valor = valor.strip().lower()
        if not valor:
            raise ValueError("Informe seu e-mail.")
        if not EMAIL_REGEXP.match(valor):
            raise ValueError("Informe um e-mail válido (ex.: voce@exemplo.com).")
        return valor

    @field_validator("senha")
    @classmethod
    def validar_senha(cls, valor: str) -> str:
        if not valor:
            raise ValueError("Informe sua senha.")
        if len(valor) < SENHA_MINIMA:
            raise ValueError(f"A senha deve ter pelo menos {SENHA_MINIMA} caracteres.")
        return valor


class CadastroRequest(BaseModel):
    nome: str
    email: str
    senha: str

    @field_validator("nome")
    @classmethod
    def validar_nome(cls, valor: str) -> str:
        valor = valor.strip()
        if len(valor) < NOME_MINIMO:
            raise ValueError(f"O nome deve ter pelo menos {NOME_MINIMO} caracteres.")
        if len(valor) > TAMANHO_MAXIMO:
            raise ValueError(f"O nome deve ter no máximo {TAMANHO_MAXIMO} caracteres.")
        return valor

    @field_validator("email")
    @classmethod
    def validar_email(cls, valor: str) -> str:
        valor = valor.strip().lower()
        if not valor:
            raise ValueError("Informe seu e-mail.")
        if not EMAIL_REGEXP.match(valor):
            raise ValueError("Informe um e-mail válido (ex.: voce@exemplo.com).")
        return valor

    @field_validator("senha")
    @classmethod
    def validar_senha(cls, valor: str) -> str:
        return validar_forca_da_senha(valor)


class TrocaSenhaRequest(BaseModel):
    """Corpo do POST /trocar-senha (1º acesso do professor e troca comum).

    A senha NOVA passa pelas MESMAS regras do cadastro (mínimo, tamanho e
    lista de triviais) — não faz sentido aceitar como nova o que o cadastro
    rejeitaria. A senha ATUAL não tem validador de força (é a que já está
    gravada; o servidor só verifica se confere com verificar_senha).
    """

    senha_atual: str
    nova_senha: str

    @field_validator("nova_senha")
    @classmethod
    def validar_nova_senha(cls, valor: str) -> str:
        return validar_forca_da_senha(valor)
