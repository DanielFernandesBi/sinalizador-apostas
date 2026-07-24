-- ============================================================
-- MIGRATION 0002 — ENDURECIMENTO DE SEGURANÇA DO BANCO
-- Fecha a exposição das views operacionais (banca, exposição, CLV,
-- tipsters, saúde) a anon/authenticated e fixa o search_path das
-- funções de trigger. Não altera schema, dados nem doutrina; a 0001
-- permanece intacta (migrations append-only — P7 estendido à infra).
--
-- Contexto (achado 1 da auditoria, confirmado no banco em 24/07/2026):
-- as views de 0001 foram criadas SECURITY DEFINER (padrão do Postgres).
-- Pelas default privileges do Supabase no schema public, anon e
-- authenticated receberam SELECT sobre elas. Como a view definer roda
-- com os privilégios do dono (postgres, que faz bypass de RLS), um
-- portador da chave anon/authenticated leria banca, exposição e CLV,
-- contornando o RLS das tabelas-base.
--
-- Correcao (defesa em profundidade):
--   1) security_invoker = on -> a view roda com os privilégios de QUEM
--      consulta; não-service bate no RLS (habilitado, sem policy) e é
--      negado. O service_role faz bypass de RLS (verificado:
--      rolbypassrls=true) e segue lendo tudo -> app intacto.
--   2) REVOKE em anon/authenticated -> nem alcançam a view.
--   3) search_path fixo nas funções de trigger -> fecha o
--      function_search_path_mutable (sequestro de search_path).
--
-- Pós-aplicação: advisor de segurança sem ERROR nem WARN; restam só os
-- INFO rls_enabled_no_policy nas tabelas, que são o desenho pretendido
-- (acesso exclusivo via service role).
-- ============================================================

-- 1) Views operacionais respeitam o RLS de quem consulta.
alter view public.vw_banca            set (security_invoker = on);
alter view public.vw_exposicao_aberta set (security_invoker = on);
alter view public.vw_clv_global       set (security_invoker = on);
alter view public.vw_clv_por_veto     set (security_invoker = on);
alter view public.vw_tipster_ranking  set (security_invoker = on);
alter view public.vw_saude_daemons    set (security_invoker = on);

-- 2) Defesa em profundidade: só o service_role fala com as views.
revoke all on public.vw_banca,
              public.vw_exposicao_aberta,
              public.vw_clv_global,
              public.vw_clv_por_veto,
              public.vw_tipster_ranking,
              public.vw_saude_daemons
  from anon, authenticated;

-- 3) search_path imutável nas funções de trigger (pg_catalog é sempre
--    implícito; 'public' resolve a tabela gates em fn_gate_so_endurece).
alter function public.fn_bloqueia_delete()  set search_path = public, pg_temp;
alter function public.fn_bloqueia_update()  set search_path = public, pg_temp;
alter function public.fn_gate_so_endurece() set search_path = public, pg_temp;
alter function public.fn_sinais_update()    set search_path = public, pg_temp;
alter function public.fn_apostas_update()   set search_path = public, pg_temp;
alter function public.fn_tips_update()      set search_path = public, pg_temp;
