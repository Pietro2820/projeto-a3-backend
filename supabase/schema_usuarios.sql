-- ============================================================================
-- Schema da tabela `usuarios` — autenticação da tela de login (Projeto A3)
--
-- ONDE RODAR: painel do Supabase → SQL Editor → New query → colar → Run.
-- É idempotente: pode rodar de novo sem erro (if not exists / on conflict).
--
-- Alimenta POST /login e POST /cadastro (app/auth/router.py). O front
-- (js/login.js na branch frontend-css) chama esses endpoints e guarda o
-- nome do usuário em sessionStorage.
--
-- Formato de `senha_hash` (gerado por app/auth/senhas.py — NUNCA guarde a
-- senha em texto plano):
--   pbkdf2_sha256$<iteracoes>$<salt_base64>$<hash_base64>
-- ============================================================================

create table if not exists public.usuarios (
    usuario_id  uuid primary key default gen_random_uuid(),
    nome        text not null,
    email       text not null,
    senha_hash  text not null,
    criado_em   timestamptz not null default now()
);

-- O backend normaliza o e-mail para minúsculas antes de gravar/consultar;
-- o unique no banco é a última linha de defesa contra duplicidade.
do $$ begin
    if not exists (
        select 1 from pg_constraint where conname = 'usuarios_email_unico'
    ) then
        alter table public.usuarios
            add constraint usuarios_email_unico unique (email);
    end if;
end $$;

-- Login consulta por e-mail a cada tentativa: índice barato que evita scan.
create index if not exists usuarios_email_idx on public.usuarios (email);

-- ----------------------------------------------------------------------------
-- USUÁRIO DE TESTE (idempotente — só insere se o e-mail ainda não existir):
--   e-mail: teste@edustream.com
--   senha : senha12345
-- O hash abaixo foi gerado por app/auth/senhas.py (PBKDF2-SHA256, 120.000
-- iterações, salt aleatório). Use estas credenciais na tela de login para
-- validar a integração; troque/sorteie outros usuários pelo próprio cadastro.
-- ----------------------------------------------------------------------------
insert into public.usuarios (nome, email, senha_hash)
select 'Usuário de Teste', 'teste@edustream.com',
       'pbkdf2_sha256$120000$Z2c0Ei4UJKuBHU8bQDksLQ==$P+sOba04bsDeY5OFkkcFwTcLi7UahZ26MgLnMh2Nlgo='
where not exists (
    select 1 from public.usuarios where email = 'teste@edustream.com'
);

-- ----------------------------------------------------------------------------
-- DESENVOLVIMENTO: RLS desligado para a chave anon conseguir ler/escrever sem
-- políticas (é o que o backend usa via supabase-py — mesmo padrão da tabela
-- videos). PRODUÇÃO/APRESENTAÇÃO com dados reais: habilite RLS e crie
-- políticas específicas.
-- ----------------------------------------------------------------------------
alter table public.usuarios disable row level security;
