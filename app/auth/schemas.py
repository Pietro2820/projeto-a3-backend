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


class LoginRequest(BaseModel):
    email: str
    senha: str

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
        if not valor:
            raise ValueError("Informe sua senha.")
        if len(valor) < SENHA_MINIMA:
            raise ValueError(f"A senha deve ter pelo menos {SENHA_MINIMA} caracteres.")
        if len(valor) > TAMANHO_MAXIMO:
            raise ValueError(f"A senha deve ter no máximo {TAMANHO_MAXIMO} caracteres.")
        return valor
