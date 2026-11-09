"""
Hash e verificação de senhas — PBKDF2-SHA256 (hashlib, stdlib).

Formato armazenado no banco (coluna `usuarios.senha_hash`):

    pbkdf2_sha256$<iteracoes>$<salt_base64>$<hash_base64>

  * salt de 16 bytes gerado aleatoriamente POR usuário (defende contra
    rainbow tables: dois usuários com a mesma senha têm hashes diferentes);
  * hash derivado com `hashlib.pbkdf2_hmac`, dklen 32;
  * 120_000 iterações — custo honesto para um projeto acadêmico: inviabiliza
    força bruta barata sem deixar o login lento (≈ 0,1 s por verificação).

Por que stdlib e não bcrypt/passlib: zero dependência nova no
requirements.txt (o deploy continua igual) e o algoritmo é auditável no
próprio código — bom para a narrativa de qualidade da disciplina.

Não use este módulo para nada fora deste projeto educacional: em produção
real, prefira Argon2id ou bcrypt com parâmetros acompanhando a OWASP.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

ALGORITMO = "pbkdf2_sha256"
ITERACOES = 120_000
TAMANHO_SALT = 16      # bytes
TAMANHO_HASH = 32      # bytes (SHA-256)


def gerar_hash(senha: str) -> str:
    """Gera a string completa `algoritmo$iteracoes$salt$hash` de uma senha."""
    salt = secrets.token_bytes(TAMANHO_SALT)
    derivado = hashlib.pbkdf2_hmac(
        "sha256", senha.encode("utf-8"), salt, ITERACOES, dklen=TAMANHO_HASH
    )
    salt_b64 = base64.b64encode(salt).decode("ascii")
    hash_b64 = base64.b64encode(derivado).decode("ascii")
    return f"{ALGORITMO}${ITERACOES}${salt_b64}${hash_b64}"


def verificar_senha(senha: str, senha_hash: str) -> bool:
    """
    Confere se `senha` corresponde a `senha_hash`.

    Qualquer coisa inesperada (formato antigo, campo vazio, string corrompida)
    devolve False em vez de explodir — um hash inválido nunca deve autenticar.
    A comparação usa `hmac.compare_digest` (constante no tempo) para não
    abrir brecha de timing attack.
    """
    if not senha or not senha_hash:
        return False

    try:
        algoritmo, iteracoes_txt, salt_b64, hash_b64 = senha_hash.split("$")
        iteracoes = int(iteracoes_txt)
        salt = base64.b64decode(salt_b64)
        esperado = base64.b64decode(hash_b64)
    except (ValueError, TypeError):
        return False

    if algoritmo != ALGORITMO or iteracoes <= 0:
        return False

    candidato = hashlib.pbkdf2_hmac(
        "sha256", senha.encode("utf-8"), salt, iteracoes, dklen=len(esperado)
    )
    return hmac.compare_digest(candidato, esperado)
