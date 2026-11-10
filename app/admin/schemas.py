"""
Contratos de entrada do painel admin (Pydantic).

Reaproveita AS MESMAS regras do cadastro público (email regex, força de
senha, tamanho de nome): a senha temporária de um professor não pode ser
mais fraca do que uma senha de estudante — o admin não cria atalhos.
"""
from __future__ import annotations

from pydantic import BaseModel, field_validator

from app.auth.schemas import (
    NOME_MINIMO,
    TAMANHO_MAXIMO,
    EMAIL_REGEXP,
    validar_forca_da_senha,
)


class NovoProfessorRequest(BaseModel):
    """Corpo do POST /admin/professores.

    `senha_temporaria` segue as MESMAS regras de força do cadastro. O
    professor será OBRIGADO a trocá-la no 1º acesso (deve_trocar_senha=true
    — ver app/auth/papeis.py), mas isso não justifica uma senha trivial no
    meio do caminho.
    """

    nome: str
    email: str
    senha_temporaria: str

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
            raise ValueError("Informe o e-mail do professor.")
        if not EMAIL_REGEXP.match(valor):
            raise ValueError("Informe um e-mail válido (ex.: professor@escola.com).")
        return valor

    @field_validator("senha_temporaria")
    @classmethod
    def validar_senha_temporaria(cls, valor: str) -> str:
        return validar_forca_da_senha(valor)
