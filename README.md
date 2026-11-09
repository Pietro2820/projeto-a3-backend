# Projeto A3 — Backend (API de Streaming de Vídeo Educacional)

API do **EduStream**, plataforma de streaming de vídeo com processamento assíncrono e qualidade adaptativa, desenvolvida para a disciplina de Garantia e Gestão da Qualidade de Software (ODS 4 — Educação de Qualidade).

> 📺 **O frontend (player + login) mora em outro repositório**: [`projeto-a3-frontend`](https://github.com/Pietro2820/projeto-a3-frontend). A visão geral do trabalho (com os links dos dois repos) está em [`projeto-a3-Garantia-da-Qualidade-de-Software-2026.2`](https://github.com/Pietro2820/projeto-a3-Garantia-da-Qualidade-de-Software-2026.2).

## Stack

| Camada | Tecnologia |
| --- | --- |
| API | Python + FastAPI |
| Processamento assíncrono | Redis + Celery |
| Transcodificação | FFmpeg (HLS multi-resolução) |
| Autenticação | Tabela `usuarios` (PBKDF2-SHA256, stdlib) |
| Banco de dados | Supabase (Postgres gerenciado) |
| Testes | Pytest (330+ testes) |
| CI/CD | GitHub Actions |

## Pré-requisitos

**Opção A — via Docker (recomendado):**
- Docker Desktop
- Git

**Opção B — rodando localmente com Python:**
- Python 3.10+
- Docker Desktop (para rodar o Redis)
- FFmpeg instalado no sistema e disponível no PATH — [instruções aqui](https://ffmpeg.org/download.html)
- Git

## Como rodar — Opção A: via Docker (recomendado)

### 1. Clonar o repositório

```bash
git clone https://github.com/Pietro2820/projeto-a3-backend
cd projeto-a3-backend
```

### 2. Criar o arquivo `.env`

```bash
cp .env.example .env
```

> Preencha `SUPABASE_URL` e `SUPABASE_KEY` com os dados do projeto Supabase do time (passo a passo na seção **Supabase** abaixo). Enquanto o `.env` estiver vazio, o backend funciona com o banco local `data/videos.json` — útil para desenvolver sem rede. **Nunca commite o `.env`**.

### 3. Subir tudo com um comando

Com o Docker Desktop aberto:

```bash
docker compose up --build
```

Isso sobe o Redis e o worker do Celery juntos, dentro de containers. Se der tudo certo, você vai ver nos logs as filas (`uploads`, `transcode`, `notifications`), as tasks disponíveis e uma linha `celery@... ready.`

Pra parar, aperte `Ctrl+C` nesse terminal. Pra rodar em segundo plano (sem travar o terminal), use `docker compose up --build -d`.

A API fica em **http://localhost:8000** — a documentação interativa (Swagger) é http://localhost:8000/docs.

## Como rodar — Opção B: localmente com Python

```bash
git clone https://github.com/Pietro2820/projeto-a3-backend
cd projeto-a3-backend

# 1. venv
python -m venv venv
venv\Scripts\activate        # Windows
source venv/bin/activate     # Linux/Mac

# 2. dependências
pip install -r requirements.txt

# 3. variáveis de ambiente
cp .env.example .env         # e preencha

# 4. Redis (com Docker aberto)
docker run -d --name redis-a3 -p 6379:6379 redis:alpine

# 5. worker do Celery (terminal separado)
celery -A app.queue.celery_app worker --loglevel=info

# 6. API (outro terminal)
uvicorn app.main:app --reload
```

## Supabase (banco de dados)

O projeto Supabase já existe — com ele configurado, **todo o time e o player passam a ver o mesmo catálogo**. Uma vez por projeto:

1. **Criar as tabelas**: painel do Supabase → **SQL Editor** → New query → cole e execute:
   - [`supabase/schema.sql`](supabase/schema.sql) — tabela `videos` + índices (idempotente);
   - [`supabase/schema_usuarios.sql`](supabase/schema_usuarios.sql) — tabela `usuarios` + usuário de teste `teste@edustream.com` / senha `senha12345`.
2. **Copiar as credenciais**: painel → **Settings → API** → `Project URL` e a chave (`anon public` serve para dev) → colar no `.env`:
   ```
   SUPABASE_URL=https://xxxx.supabase.co
   SUPABASE_KEY=eyJ...
   ```
3. **Conferir a ligação**:
   ```bash
   python scripts/verificar_supabase.py
   ```
4. **Reiniciar** a API e o worker para valerem as novas variáveis.

> Sem credenciais no `.env`, os vídeos continuam funcionando com o banco JSON local — mas o **login exige Supabase configurado** (não existe usuário em arquivo: a API responde `503` com instruções até o banco estar no ar).

## Autenticação (login e cadastro)

| Endpoint | O que faz | Erros |
| --- | --- | --- |
| `POST /cadastro` | Cria a conta (nome, e-mail, senha — hash PBKDF2-SHA256 com salt por usuário, stdlib sem dependência nova) | `409` e-mail já existe · `422` validação · `503` banco indisponível |
| `POST /login` | Valida as credenciais e devolve `{usuario:{usuario_id, nome, email}}` | `401` e-mail ou senha incorretos (mesma mensagem nos dois casos — não revela quais e-mails têm conta) · `503` banco indisponível |

Teste rápido com o usuário do SQL:

```bash
curl -X POST http://localhost:8000/login \
  -H "Content-Type: application/json" \
  -d '{"email":"teste@edustream.com","senha":"senha12345"}'
```

Formato do hash no banco (`app/auth/senhas.py`): `pbkdf2_sha256$<iterações>$<salt_base64>$<hash_base64>` — 120.000 iterações, salt de 16 bytes por usuário, comparação em tempo constante.

## Rotas principais

| Rota | Descrição |
| --- | --- |
| `POST /login`, `POST /cadastro` | Autenticação (tabela `usuarios`) |
| `POST /upload` | Envia vídeo, salva metadados e enfileira transcodificação |
| `GET /status/{video_id}` | Estado do processamento (Redis, fallback banco) |
| `GET /media/{video_id}` | Diagnóstico da mídia no disco (master.m3u8, segmentos) |
| `GET /catalogo` | Catálogo de vídeos (`?prontos=1&q=busca`) |
| `GET /trending`, `GET /recomendacoes/{user_id}` | Recomendações (Jaccard por tags + views) |
| `POST /watch` | Registra visualização |
| `/player/` | Serve o frontend (opcional — veja abaixo) |

### Servindo o frontend pelo backend (opcional)

Depois da divisão dos repositórios, o backend não carrega mais o player dentro dele. Se quiser servir tudo em uma origem só (sem CORS), clone o frontend ao lado e aponte o `PLAYER_DIR` no `.env`:

```
PLAYER_DIR=../projeto-a3-frontend
```

Reinicie a API e o player fica em http://localhost:8000/player/.

## Rodar os testes

```bash
pytest -v --cov=app
```

São 330+ testes cobrindo upload, fila, transcodificação (skipadas sem FFmpeg no PATH), recomendações, status e autenticação. O CI roda a mesma suíte com Redis de verdade em cada push.

## Estrutura de pastas

```
projeto-a3-backend/
├── app/
│   ├── main.py            # FastAPI: rotas + CORS + /videos + /player opcional
│   ├── auth/              # POST /login e /cadastro (tabela usuarios)
│   ├── upload/            # POST /upload (salva arquivo e metadados, enfileira)
│   ├── queue/             # Celery: tasks, broker e status em Redis
│   ├── transcoding/       # FFmpeg: HLS multi-resolução + thumbnail
│   ├── recommendations/   # Jaccard por tags + views (/trending, /relacionados)
│   ├── status/            # GET /status/{video_id} (Redis, fallback banco)
│   └── database/          # Supabase + fallback local (data/videos.json)
├── scripts/               # demo E2E, seed, diagnósticos
├── supabase/              # schema.sql (videos) + schema_usuarios.sql (login)
├── docker-compose.yml     # redis + worker + api
├── Dockerfile
├── requirements.txt
└── .env.example
```

## Problemas comuns

- **`503` no login/cadastro** — `SUPABASE_URL`/`SUPABASE_KEY` ausentes no `.env` ou `schema_usuarios.sql` ainda não rodado no SQL Editor.
- **`401` mesmo com usuário criado** — o e-mail é normalizado para minúsculas no backend; confira se cadastrou com o mesmo e-mail que está digitando.
- **Upload não processa** — worker do Celery parado? Veja `docker compose ps` ou suba o Redis (`docker run -d -p 6379:6379 redis:alpine`).
- **`o vídeo não toca`** — rode `python scripts/verificar_player.py` (aponta exatamente o que falta: API, banco ou mídia).
