# scripts/ — Teste E2E da autenticação (sem depender do Supabase real)

Prova de ponta a ponta do fluxo **cadastro → hash PBKDF2 → login** contra a
API real (`uvicorn`), usando um **Supabase falso** que fala o mesmo protocolo
REST (PostgREST) que o `supabase-py` usa em produção — inclusive o erro 409
`23505` de e-mail duplicado.

## Como rodar (3 terminais, ou copie e cole)

```bash
# 1) "Supabase" falso (porta 8902) — tabela usuarios em memória
python3 -m uvicorn --app-dir scripts fake_supabase:app --port 8902

# 2) API real (porta 8000) apontando para o falso
SUPABASE_URL=http://localhost:8902 SUPABASE_KEY=teste \
PLAYER_DIR=../projeto-a3-frontend \
python3 -m uvicorn app.main:app --port 8000

# 3) Bateria completa (11 verificações)
bash scripts/e2e_auth.sh
```

O que a bateria prova (11 ✔ esperados):

1. `POST /cadastro` cria conta (201) e devolve só os campos públicos;
2. a `senha_hash` gravada no banco é `pbkdf2_sha256$120000$<salt>$<hash>` —
   a senha **nunca** vai em texto plano;
3. `POST /login` com a senha certa → 200;
4. com a senha errada → 401 "E-mail ou senha incorretos.";
5. e-mail inexistente → 401 com a MESMA mensagem (anti-enumeração de contas);
6. e-mail duplicado no cadastro → 409;
7. validações → 422 (nome curto, e-mail inválido, senha < 8);
8. e-mail é normalizado para minúsculas (login com CAIXA ALTA funciona);
9. verificação PBKDF2 direta: hash corrompido nunca autentica (falha
   segura) e a mesma senha gera hashes diferentes (salt aleatório).

Com o `PLAYER_DIR` apontando para o clone do frontend, o front fica em
http://localhost:8000/player/ — mesma origem da API, sem CORS.
