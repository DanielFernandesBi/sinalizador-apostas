-- ============================================================
-- MIGRATION 0003 — ANTI-DUPLICIDADE DO L1 (achado 7 da auditoria)
-- O L1 relê a janela de 1h a cada ciclo e reprocessa todos os grupos. Sem chave de
-- candidato nem unicidade, uma oportunidade persistente vira um sinal (e um aborto)
-- por minuto. A chave de candidato identifica a APOSTA:
--   evento | mercado | linha | selecao | casa
-- Regra: no maximo UM sinal ABERTO (aguardando_crivo|confirmado) por candidato; ao
-- fechar (vetado/expirado/erro) ele sai do indice e um novo sinal pode nascer quando
-- a situacao muda. Abortos usam a chave so para dedup na janela (nao ha unicidade —
-- aborto e evento append-only, P7).
-- ============================================================

alter table sinais add column chave_candidato text;
alter table abortos_l1 add column chave_candidato text;

-- No maximo um sinal ABERTO por candidato (indice parcial). NULL nao conta.
create unique index ux_sinais_candidato_aberto on sinais (chave_candidato)
  where chave_candidato is not null and status in ('aguardando_crivo', 'confirmado');

-- Suporta o dedup de abortos por candidato na janela (SELECT ... where ts >= desde).
create index ix_abortos_candidato on abortos_l1 (chave_candidato, ts desc);
