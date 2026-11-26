-- ============================================================================
-- Migração 003 — apagar um usuário não pode travar por causa dos vídeos dele
--
-- Problema: a migração 002 criou videos.professor_id como
--     references public.usuarios (usuario_id)          -- sem ON DELETE
-- e depois o deixou NOT NULL. Resultado: apagar um professor que tem vídeos
-- (pelo painel do Supabase ou por SQL) falha com
--     violates foreign key constraint "videos_professor_id_fkey"
--
-- Decisão: ON DELETE SET NULL. Os vídeos CONTINUAM no catálogo (aluno não
-- perde aula) e viram "sem dono". O backend já trata esse caso
-- (app/videos/router.py: professor_id ausente = vídeo legado, só o ADMIN
-- edita/apaga), então nenhuma mudança de código é necessária.
-- (Se o grupo preferir apagar os vídeos junto, troque SET NULL por CASCADE —
-- mas aí os arquivos HLS no storage ficam órfãos.)
--
-- Como rodar: Supabase → SQL Editor → colar e executar (uma vez).
-- Idempotente: pode rodar de novo sem erro.
-- ============================================================================

-- 1) SET NULL exige que a coluna aceite NULL.
alter table public.videos
    alter column professor_id drop not null;

-- 2) Recria a FK com a regra de exclusão.
alter table public.videos
    drop constraint if exists videos_professor_id_fkey;

alter table public.videos
    add constraint videos_professor_id_fkey
    foreign key (professor_id)
    references public.usuarios (usuario_id)
    on delete set null;
