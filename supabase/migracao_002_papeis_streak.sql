-- ============================================================================
-- MIGRAÇÃO 002 — Papéis (admin/professor/estudante), 1º acesso, propriedade
-- de vídeos e dias de estudo (streak) — Projeto A3 / EduStream
--
-- ONDE RODAR: painel do Supabase → SQL Editor → New query → colar → Run.
-- Pré-requisitos (nesta ordem): supabase/schema.sql e supabase/
-- schema_usuarios.sql já rodados. Esta migração É IDEMPOTENTE: pode rodar
-- de novo sem erro (if not exists / where not exists em tudo).
--
-- O que ela cria:
--   1. usuarios.role        — 'admin' > 'professor' > 'estudante' (padrão)
--   2. usuarios.deve_trocar_senha — 1º acesso do professor força a troca
--   3. ADMIN INICIAL (seed) — conta admin@edustream.com criada aqui
--   4. videos.professor_id  — dono do vídeo (upload só de professor/admin)
--   5. dias_estudo          — um registro por (aluno, dia) alimenta o streak
--
-- SEGURANÇA: o papel NUNCA vem do cliente. POST /cadastro grava 'estudante'
-- fixo no app (app/auth/usuarios.py); professor só nasce por
-- POST /admin/professores (exigindo papel admin); o CHECK do banco é a
-- última linha de defesa contra um valor inválido escapar.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1) Papel e flag de 1º acesso na tabela usuarios
-- ----------------------------------------------------------------------------
alter table public.usuarios
    add column if not exists role text not null default 'estudante';

alter table public.usuarios
    add column if not exists deve_trocar_senha boolean not null default false;

do $$ begin
    if not exists (
        select 1 from pg_constraint where conname = 'usuarios_role_valido'
    ) then
        alter table public.usuarios
            add constraint usuarios_role_valido
            check (role in ('admin', 'professor', 'estudante'));
    end if;
end $$;

-- Listar usuários do painel e resolver login por e-mail: índices baratos.
create index if not exists usuarios_role_idx on public.usuarios (role);

-- ----------------------------------------------------------------------------
-- 2) ADMIN INICIAL (seed, idempotente)
--
--    E-mail : admin@edustream.com
--    Senha  : EduStream#2026   (TEMPORÁRIA — o 1º acesso EXIGE a troca:
--            deve_trocar_senha = true bloqueia as demais rotas até trocar)
--
--    O hash abaixo foi gerado por app/auth/senhas.py (PBKDF2-SHA256,
--    120.000 iterações, salt aleatório) — o mesmo formato do usuário
--    teste@edustream.com do schema_usuarios.sql.
-- ----------------------------------------------------------------------------
insert into public.usuarios (nome, email, senha_hash, role, deve_trocar_senha)
select 'Administrador', 'admin@edustream.com',
       'pbkdf2_sha256$120000$DLWg9WhhnVDxDINRvOJSQg==$JJVVmoUQk9MRf/CrTTp1Av85YcHJpkzbSlPR4VjGyN8=',
       'admin', true
where not exists (
    select 1 from public.usuarios where email = 'admin@edustream.com'
);

-- ----------------------------------------------------------------------------
-- 3) Propriedade dos vídeos — todo vídeo pertence a um professor (ou admin)
-- ----------------------------------------------------------------------------
alter table public.videos
    add column if not exists professor_id uuid
    references public.usuarios (usuario_id);

-- Vídeos que JÁ existiam (sem dono) passam a pertencer ao admin do seed:
-- o catálogo continua no ar e, a partir daqui, 100% dos vídeos têm dono.
update public.videos
   set professor_id = (
       select usuario_id from public.usuarios
        where email = 'admin@edustream.com'
        limit 1
   )
 where professor_id is null;

-- NOT NULL só depois de preencher — e só se não sobrou órfão (uma base
-- vazia ou já preenchida passa direto; nunca falha no meio da migração).
do $$ begin
    if not exists (select 1 from public.videos where professor_id is null) then
        alter table public.videos alter column professor_id set not null;
    end if;
end $$;

-- "Meus vídeos" do professor consulta por dono; o índice evita scan.
create index if not exists videos_professor_idx on public.videos (professor_id);

-- ----------------------------------------------------------------------------
-- 4) Dias de estudo (streak do estudante)
--
--    Um registro por (aluno, dia) — a chave primária composta torna o
--    registro IDEMPOTENTE: concluir 3 vídeos no mesmo dia continua sendo
--    1 dia de estudo. O dia é calculado no fuso America/Sao_Paulo pelo
--    backend (app/streak/calculo.py); o cliente não manda data.
--
--    PRIVACIDADE: só estudante tem dias gravados — professor/admin não
--    têm streak (a regra vive na API, app/streak/router.py).
-- ----------------------------------------------------------------------------
create table if not exists public.dias_estudo (
    usuario_id    uuid not null
                  references public.usuarios (usuario_id) on delete cascade,
    dia           date not null,
    registrado_em timestamptz not null default now(),
    primary key (usuario_id, dia)
);

-- Cálculo do streak caminha de dia em dia para trás: índice serve ao PK.
create index if not exists dias_estudo_dia_idx on public.dias_estudo (dia);

-- ----------------------------------------------------------------------------
-- DESENVOLVIMENTO: RLS desligado (mesmo padrão das tabelas usuarios/videos —
-- o backend usa a chave anon via supabase-py). PRODUÇÃO com dados reais:
-- habilite RLS e crie políticas específicas por papel.
-- ----------------------------------------------------------------------------
alter table public.dias_estudo disable row level security;
