-- 0009 — Reconciliação: gates semeados direto + índices de FK (achado #5 do 2º ciclo).
--
-- PROBLEMA: sete gates foram semeados DIRETO no banco vivo (pelos ritos das Sugestões
-- nº 3 e nº 5) e nunca viraram migration. A 0001 semeava 9; o banco tinha 16. Uma
-- instalação nova a partir do GitHub subia com 9 gates — e `gates.validar_integridade()`
-- exige TODOS os nomeados, então NENHUM daemon subiria. O banco vivo e o repositório
-- descreviam sistemas diferentes.
--
-- (As migrations 0002–0008 já corrigiram a outra metade do achado: no momento da
-- auditoria só a 0001 estava registrada; hoje as nove estão. A varredura cruzada
-- confirma que todo objeto de schema vivo — tabelas, índices, funções, views — tem
-- origem em migration. As casas além das duas semeadas NÃO são drift: o L0 as cria em
-- runtime por get-or-create (`garantir_casa`). A governança em `config_sistema` vem do
-- rito, por `scripts/sync_governanca.py`.)
--
-- Idempotente: `on conflict (nome, versao) do nothing` — no banco vivo os sete já
-- existem e nada muda; numa instalação nova, são criados. Os valores são exatamente os
-- vigentes hoje, conferidos um a um contra o banco.
--
-- O trigger `tg_gate_endurece` (0001) não interfere: só barra AFROUXAR gate pétreo, e
-- todos os sete são não-pétreos.

insert into gates (nome, valor, petreo, direcao_endurecer, versao, motivo) values
  -- Sugestão nº 3 (PC-EXP / Correção #6) — exposição aberta em camadas.
  ('exposicao_max_jogo_pct',     3.0,  false, 'menor', 1, 'Sugestão nº 3 (PC-EXP) — Correção #6 — a calibrar'),
  ('exposicao_max_liga_dia_pct', 6.0,  false, 'menor', 1, 'Sugestão nº 3 (PC-EXP) — Correção #6 — a calibrar'),
  ('exposicao_max_dia_pct',      10.0, false, 'menor', 1, 'Sugestão nº 3 (PC-EXP) — Correção #6 — a calibrar'),
  -- Sugestão nº 3 — gatilho odds_drop e detector de gatilho_anomalo.
  ('drop_min_pct',               3.0,  false, 'maior', 1, 'Sugestão nº 3 — gatilho odds_drop — a calibrar'),
  ('janela_drop_s',              900,  false, 'menor', 1, 'Sugestão nº 3 — gatilho odds_drop — a calibrar'),
  ('anomalia_move_pct',          3.0,  false, 'menor', 1, 'Sugestão nº 3 — detector gatilho_anomalo — a calibrar'),
  -- Sugestão nº 5 (PC-RASTREIO) — piso de edge para rastrear CLV de near-miss.
  ('rastreio_edge_min_pct',      1.0,  false, 'menor', 1, 'Sugestão nº 5 (PC-RASTREIO) — piso de amostragem do clv_rastrear em near-misses; não é gate de entrada')
on conflict (nome, versao) do nothing;

-- ---------- Índices de chave estrangeira ----------

-- Toda FK sem índice de cobertura vira varredura completa da tabela filha quando o pai
-- é consultado ou apagado (advisor de performance). Ainda não dói porque as tabelas
-- estão quase vazias, mas `odds_snapshots` é a de maior volume por desenho (milhares
-- de linhas por ciclo) e `sinais.evento_id` é lida a cada fechamento do L4.
-- `if not exists` mantém a migration idempotente.

create index if not exists ix_abortos_evento      on abortos_l1 (evento_id);
create index if not exists ix_apostas_sinal       on apostas (sinal_id);
create index if not exists ix_apostas_casa        on apostas (casa_id);
create index if not exists ix_clv_resultado_log   on clv_resultados (clv_log_id);
create index if not exists ix_notificacoes_sinal  on notificacoes (sinal_id);
create index if not exists ix_snap_casa           on odds_snapshots (casa_id);
create index if not exists ix_sinais_evento       on sinais (evento_id);
create index if not exists ix_sinais_casa_venue   on sinais (casa_venue_id);
create index if not exists ix_tips_tipster        on tips (tipster_id);
create index if not exists ix_tips_evento         on tips (evento_id);
create index if not exists ix_tips_sinal          on tips (sinal_id);
