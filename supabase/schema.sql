-- ============================================================================
-- Schema da tabela `videos` — Plataforma de Vídeo Educacional (Projeto A3)
--
-- ONDE RODAR: painel do Supabase → SQL Editor → New query → colar → Run.
-- É idempotente: pode rodar de novo sem erro (if not exists / if not exists).
--
-- Esta tabela é o contrato #2 da documentação técnica: é nela que o upload
-- grava os metadados, o worker atualiza o status (pending → processing →
-- completed/failed) e o player lê o catálogo (status = 'completed').
-- ============================================================================

create table if not exists public.videos (
    -- O upload gera o UUID em Python e envia pronto; mas inserções manuais
    -- pelo painel (ou criar_video() sem id) também funcionam graças ao default.
    video_id   uuid primary key default gen_random_uuid(),

    titulo     text not null,
    descricao  text,

    -- jsonb de propósito: o projeto grava tags de DOIS jeitos e os dois
    -- precisam funcionar — lista (parse_tags do upload) e string separada
    -- por vírgula (inserções manuais). O motor de recomendação
    -- (normalizar_tags) aceita ambos.
    tags       jsonb,

    categoria  text,
    autor      text,

    criado_em  timestamptz not null default now(),

    -- Os quatro estados do contrato #5; qualquer outro valor é bug e o
    -- próprio banco barra (melhor falhar na escrita do que no player).
    status     text not null default 'pending'
               check (status in ('pending', 'processing', 'completed', 'failed'))
);

-- Catálogo do player filtra por status e ordena por criação: índices baratos
-- que evitam scan completo quando o catálogo crescer.
create index if not exists videos_status_idx    on public.videos (status);
create index if not exists videos_criado_em_idx  on public.videos (criado_em desc);

-- ----------------------------------------------------------------------------
-- DESENVOLVIMENTO: RLS desligado para a chave anon conseguir ler/escrever sem
-- políticas (é o que o backend usa via supabase-py).
-- PRODUÇÃO/APRESENTAÇÃO com dados reais: habilite RLS e crie políticas
-- específicas (ex.: leitura pública, escrita só com service_role).
-- ----------------------------------------------------------------------------
alter table public.videos disable row level security;
