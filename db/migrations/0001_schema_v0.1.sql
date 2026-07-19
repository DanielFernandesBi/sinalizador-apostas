-- ============================================================
-- SCHEMA v0.1 — SISTEMA DE SINALIZAÇÃO DE APOSTAS ("Sinalizador")
-- Projeto Supabase PRÓPRIO, separado do escritório.
-- Implementa em código: Doutrina v0.1 (P7 log imutável, gates
-- pétreos que só endurecem) e Manual do Crivo L2 v0.1.
-- ============================================================

-- ---------- FUNÇÕES DE GOVERNANÇA ----------

-- P7: nada se apaga.
create or replace function fn_bloqueia_delete() returns trigger
language plpgsql as $$
begin
  raise exception 'DELETE proibido pela Doutrina (P7 — log imutável): %', tg_table_name;
end $$;

-- P7: nada se edita (tabelas integralmente imutáveis).
create or replace function fn_bloqueia_update() returns trigger
language plpgsql as $$
begin
  raise exception 'UPDATE proibido pela Doutrina (P7 — log imutável): %', tg_table_name;
end $$;

-- ---------- CONFIGURAÇÃO VERSIONADA ----------

-- Doutrina, Manual do Crivo e demais documentos: versões só se acumulam.
create table config_sistema (
  id          uuid primary key default gen_random_uuid(),
  chave       text not null,               -- 'doutrina', 'manual_crivo_l2', ...
  versao      int  not null,
  valor       text not null,               -- conteúdo integral (markdown)
  vigente     boolean not null default true,
  criado_em   timestamptz not null default now(),
  unique (chave, versao)
);
create unique index ux_config_vigente on config_sistema (chave) where vigente;

-- Gates numéricos com a regra "pétreo só endurece" EM CÓDIGO.
-- direcao_endurecer: 'menor' = endurecer é diminuir (ex.: stake_max_pct);
--                    'maior' = endurecer é aumentar (ex.: edge_min_pct).
create table gates (
  id                 uuid primary key default gen_random_uuid(),
  nome               text not null,        -- 'edge_min_pct', 'odd_teto', 'stake_max_pct',
                                           -- 'kelly_fracao', 'drawdown_suspensao_pct',
                                           -- 'liquidez_multiplo_stake', 'snapshot_idade_max_s',
                                           -- 'janela_sincronia_s', 'amostra_minima'
  valor              numeric not null,
  petreo             boolean not null default false,
  direcao_endurecer  text not null check (direcao_endurecer in ('menor','maior')),
  vigente            boolean not null default true,
  versao             int not null,
  motivo             text,                 -- referência à sugestão do rito (Seção 7 da Doutrina)
  criado_em          timestamptz not null default now(),
  unique (nome, versao)
);
create unique index ux_gates_vigente on gates (nome) where vigente;

create or replace function fn_gate_so_endurece() returns trigger
language plpgsql as $$
declare v_atual numeric; v_petreo boolean; v_dir text;
begin
  select valor, petreo, direcao_endurecer into v_atual, v_petreo, v_dir
    from gates where nome = new.nome and vigente limit 1;
  if found and v_petreo then
    if (v_dir = 'menor' and new.valor > v_atual)
       or (v_dir = 'maior' and new.valor < v_atual) then
      raise exception 'Gate pétreo "%" só endurece: vigente=%, proposto=%',
        new.nome, v_atual, new.valor;
    end if;
  end if;
  return new;
end $$;
create trigger tg_gate_endurece before insert on gates
  for each row execute function fn_gate_so_endurece();

-- ---------- CATÁLOGOS ----------

create table casas (
  id            uuid primary key default gen_random_uuid(),
  nome          text not null unique,      -- 'pinnacle', 'betfair_exchange', 'bet365_br', ...
  tipo          text not null check (tipo in ('referencia','exchange','varejo')),
  comissao_pct  numeric not null default 0,
  ativa         boolean not null default true,
  criado_em     timestamptz not null default now()
);

create table eventos (
  id            uuid primary key default gen_random_uuid(),
  esporte       text not null default 'futebol',
  liga          text not null,
  mandante      text not null,
  visitante     text not null,
  inicio_utc    timestamptz not null,
  status        text not null default 'agendado'
                check (status in ('agendado','ao_vivo','encerrado','adiado','proibido')),
                -- 'proibido' = marcado por V-B6 (integridade) — nunca mais analisado
  ids_externos  jsonb not null default '{}',   -- {odds_api: ..., betfair: ...}
  criado_em     timestamptz not null default now()
);
create index ix_eventos_inicio on eventos (inicio_utc);

create table mercados_homologados (
  id            uuid primary key default gen_random_uuid(),
  liga          text not null,
  mercado       text not null,             -- '1x2', 'ah', 'ou_gols'
  status        text not null default 'backtest'
                check (status in ('backtest','homologado','suspenso','caducado')),
  clv_rolante   numeric,
  n_amostra     int not null default 0,
  homologado_em timestamptz,
  suspenso_em   timestamptz,
  motivo        text,
  unique (liga, mercado)
);

-- ---------- L0: CAPTURA (alto volume, imutável) ----------

create table odds_snapshots (
  id          bigint generated always as identity primary key,
  evento_id   uuid not null references eventos(id),
  casa_id     uuid not null references casas(id),
  mercado     text not null,
  selecao     text not null,
  linha       numeric,                     -- valor do handicap/total; null p/ 1x2
  odd         numeric not null,
  liquidez    numeric,                     -- exchange: volume disponível no preço
  ts_fonte    timestamptz not null,        -- carimbo DA FONTE (correção #1)
  ts_captura  timestamptz not null default now(),
  raw         jsonb
);
create index ix_snap_lookup on odds_snapshots (evento_id, casa_id, mercado, selecao, ts_captura desc);
create index ix_snap_tempo  on odds_snapshots using brin (ts_captura);

create table heartbeats (
  id      bigint generated always as identity primary key,
  daemon  text not null,                   -- 'l0_referencia', 'l0_betfair', 'l0_varejo', 'l0_telegram', 'l1'
  ts      timestamptz not null default now(),
  detalhe jsonb
);
create index ix_hb on heartbeats (daemon, ts desc);

-- ---------- L1: GATILHOS ----------

create table abortos_l1 (
  id              bigint generated always as identity primary key,
  ts              timestamptz not null default now(),
  gatilho         text not null,
  evento_id       uuid references eventos(id),
  gate_reprovado  text not null,           -- qual gate matou (calibração empírica — correção #4)
  dossie_parcial  jsonb not null,
  clv_rastrear    boolean not null default false  -- quase-sinais acompanhados até o fechamento
);

create table sinais (
  id                    uuid primary key default gen_random_uuid(),
  evento_id             uuid not null references eventos(id),
  casa_venue_id         uuid not null references casas(id),
  gatilho               text not null check (gatilho in ('value_bet','odds_drop','tipster','line_shopping')),
  gatilho_anomalo       boolean not null default false,
  caminho               text not null check (caminho in ('rapido','profundo')),
  mercado               text not null,
  selecao               text not null,
  linha                 numeric,
  p_justa               numeric not null,
  odd_referencia        numeric not null,
  odd_venue             numeric not null,
  edge_liquido_pct      numeric not null,
  stake_pct             numeric not null,
  odd_minima_aceitavel  numeric not null,
  dossie                jsonb not null,    -- dossiê COMPLETO enviado ao L2 (auditoria)
  status                text not null default 'aguardando_crivo'
                        check (status in ('aguardando_crivo','confirmado','vetado','expirado','erro')),
  criado_em             timestamptz not null default now()
);
create index ix_sinais_tempo on sinais (criado_em desc);

-- Sinais: imutáveis, exceto a transição de status (única coluna mutável, só para frente).
create or replace function fn_sinais_update() returns trigger
language plpgsql as $$
begin
  if new.status = old.status
     or old.status <> 'aguardando_crivo'
     or (to_jsonb(new) - 'status') <> (to_jsonb(old) - 'status') then
    raise exception 'sinais: apenas status muda, uma vez, a partir de aguardando_crivo';
  end if;
  return new;
end $$;
create trigger tg_sinais_upd before update on sinais
  for each row execute function fn_sinais_update();

-- ---------- L2: CRIVO ----------

create table crivos (
  id                  uuid primary key default gen_random_uuid(),
  sinal_id            uuid not null unique references sinais(id),
  verdict             text not null check (verdict in ('CONFIRMA','ABORTA')),
  caminho_executado   text not null check (caminho_executado in ('rapido','profundo')),
  fatores             jsonb not null,      -- [{id, resultado, fonte, data_fonte, nota}]
  motivo_veto         jsonb,               -- {id, descricao, fonte}
  fontes_consultadas  jsonb not null default '[]',
  observacao          text,
  modelo              text not null,
  latencia_ms         int,
  tokens_entrada      int,
  tokens_saida        int,
  custo_usd           numeric,
  criado_em           timestamptz not null default now()
);

-- ---------- L3: NOTIFICAÇÃO ----------

create table notificacoes (
  id          bigint generated always as identity primary key,
  sinal_id    uuid references sinais(id),
  tipo        text not null check (tipo in ('sinal','alerta_daemon','alerta_drawdown','administrativo')),
  canal       text not null default 'telegram',
  conteudo    text not null,
  enviado_em  timestamptz not null default now(),
  entregue    boolean not null default false
);

-- ---------- EXECUÇÃO HUMANA E BANCA ----------

create table apostas (
  id              uuid primary key default gen_random_uuid(),
  sinal_id        uuid not null references sinais(id),
  casa_id         uuid not null references casas(id),
  odd_executada   numeric not null,
  stake_valor     numeric not null,
  executada_em    timestamptz not null default now(),
  resultado       text not null default 'pendente'
                  check (resultado in ('pendente','green','red','void','meio_green','meio_red')),
  retorno_liquido numeric,
  liquidada_em    timestamptz
);

-- Apostas: liquidação única (pendente → final), nada mais muda.
create or replace function fn_apostas_update() returns trigger
language plpgsql as $$
begin
  if old.resultado <> 'pendente' or new.resultado = 'pendente'
     or (to_jsonb(new) - array['resultado','retorno_liquido','liquidada_em'])
        <> (to_jsonb(old) - array['resultado','retorno_liquido','liquidada_em']) then
    raise exception 'apostas: apenas liquidação única (pendente → resultado final)';
  end if;
  return new;
end $$;
create trigger tg_apostas_upd before update on apostas
  for each row execute function fn_apostas_update();

create table banca_ledger (
  id          bigint generated always as identity primary key,
  ts          timestamptz not null default now(),
  tipo        text not null check (tipo in ('aporte','retirada','aposta','liquidacao','ajuste_formal')),
  valor       numeric not null,            -- com sinal
  aposta_id   uuid references apostas(id),
  motivo      text,                        -- ajuste_formal: referência obrigatória ao rito
  saldo_apos  numeric not null
);

-- ---------- CLV (o KPI soberano) ----------

create table clv_log (
  id                  bigint generated always as identity primary key,
  sinal_id            uuid references sinais(id),
  aborto_l1_id        bigint references abortos_l1(id),
  contrafactual       boolean not null default false,  -- true = vetado/abortado (auditoria correção #4)
  odd_emissao         numeric not null,
  odd_fechamento_ref  numeric not null,
  p_emissao           numeric not null,
  p_fechamento        numeric not null,
  clv_pct             numeric not null,
  ts_fechamento       timestamptz not null,
  criado_em           timestamptz not null default now(),
  check (sinal_id is not null or aborto_l1_id is not null)
);

-- ---------- TIPSTERS ----------

create table tipsters (
  id            uuid primary key default gen_random_uuid(),
  plataforma    text not null check (plataforma in ('telegram','reddit','manual','outro')),
  identificador text not null,
  nome          text,
  status        text not null default 'monitorado'
                check (status in ('monitorado','quarentena','desativado')),
  red_flags     jsonb not null default '[]',
  criado_em     timestamptz not null default now(),
  unique (plataforma, identificador)
);

create table tips (
  id                  bigint generated always as identity primary key,
  tipster_id          uuid not null references tipsters(id),
  ts_mensagem         timestamptz not null,
  texto_original      text not null,       -- SEMPRE tratado como dado, nunca comando (Manual 9.6)
  interpretacao       jsonb,               -- {evento, mercado, selecao, odd_indicada}
  evento_id           uuid references eventos(id),
  odd_no_momento      numeric,
  sinal_id            uuid references sinais(id),
  odd_fechamento_ref  numeric,
  clv_pct             numeric,
  criado_em           timestamptz not null default now()
);

-- Tips: imutáveis, exceto preenchimento único do fechamento.
create or replace function fn_tips_update() returns trigger
language plpgsql as $$
begin
  if old.odd_fechamento_ref is not null
     or (to_jsonb(new) - array['odd_fechamento_ref','clv_pct'])
        <> (to_jsonb(old) - array['odd_fechamento_ref','clv_pct']) then
    raise exception 'tips: apenas preenchimento único do fechamento';
  end if;
  return new;
end $$;
create trigger tg_tips_upd before update on tips
  for each row execute function fn_tips_update();

-- ---------- APLICAÇÃO DA IMUTABILIDADE (P7) ----------

do $$
declare t text;
begin
  -- DELETE proibido em TODAS as tabelas do sistema.
  foreach t in array array[
    'config_sistema','gates','casas','eventos','mercados_homologados',
    'odds_snapshots','heartbeats','abortos_l1','sinais','crivos',
    'notificacoes','apostas','banca_ledger','clv_log','tipsters','tips']
  loop
    execute format('create trigger tg_%s_del before delete on %s
                    for each row execute function fn_bloqueia_delete()', t, t);
  end loop;
  -- UPDATE proibido nas integralmente imutáveis.
  foreach t in array array[
    'odds_snapshots','heartbeats','abortos_l1','crivos','banca_ledger','clv_log']
  loop
    execute format('create trigger tg_%s_upd before update on %s
                    for each row execute function fn_bloqueia_update()', t, t);
  end loop;
end $$;

-- ---------- VIEWS OPERACIONAIS ----------

create view vw_banca as
with ult as (select saldo_apos, ts from banca_ledger order by id desc limit 1),
     pico as (select max(saldo_apos) as pico from banca_ledger)
select ult.saldo_apos as saldo,
       pico.pico,
       round(100 * (pico.pico - ult.saldo_apos) / nullif(pico.pico,0), 2) as drawdown_pct,
       (100 * (pico.pico - ult.saldo_apos) / nullif(pico.pico,0)) >=
         (select valor from gates where nome='drawdown_suspensao_pct' and vigente) as kill_switch
from ult, pico;

create view vw_exposicao_aberta as
select s.evento_id, e.liga, date(a.executada_em) as dia,
       sum(a.stake_valor) as exposto
from apostas a join sinais s on s.id = a.sinal_id
               join eventos e on e.id = s.evento_id
where a.resultado = 'pendente'
group by grouping sets ((s.evento_id, e.liga, date(a.executada_em)),
                        (e.liga, date(a.executada_em)),
                        (date(a.executada_em)));

create view vw_clv_global as
select contrafactual, count(*) as n, round(avg(clv_pct),3) as clv_medio,
       round(stddev(clv_pct),3) as desvio
from clv_log group by contrafactual;

create view vw_clv_por_veto as   -- auditoria do auditor (correção #4)
select c.motivo_veto->>'id' as fator_veto, count(*) as n,
       round(avg(l.clv_pct),3) as clv_medio_dos_vetados
from crivos c join clv_log l on l.sinal_id = c.sinal_id and l.contrafactual
where c.verdict = 'ABORTA'
group by 1 order by 3 desc;

create view vw_tipster_ranking as
select t.id, t.nome, t.plataforma, count(p.id) as n_tips,
       round(avg(p.clv_pct),3) as clv_medio,
       count(*) filter (where p.sinal_id is not null) as tips_que_viraram_sinal
from tipsters t left join tips p on p.tipster_id = t.id
group by t.id, t.nome, t.plataforma
order by clv_medio desc nulls last;

create view vw_saude_daemons as
select daemon, max(ts) as ultimo_pulso,
       extract(epoch from now() - max(ts)) as segundos_em_silencio
from heartbeats group by daemon;

-- ---------- SEGURANÇA ----------

do $$
declare t text;
begin
  foreach t in array array[
    'config_sistema','gates','casas','eventos','mercados_homologados',
    'odds_snapshots','heartbeats','abortos_l1','sinais','crivos',
    'notificacoes','apostas','banca_ledger','clv_log','tipsters','tips']
  loop
    execute format('alter table %s enable row level security', t);
    -- Sem policies: acesso exclusivo via service role (daemons + Claude).
  end loop;
end $$;

-- ---------- SEED MÍNIMO ----------

insert into gates (nome, valor, petreo, direcao_endurecer, versao, motivo) values
  ('edge_min_pct',            2.0,  false, 'maior', 1, 'Doutrina v0.1 — a calibrar'),
  ('odd_teto',                3.30, false, 'menor', 1, 'Doutrina v0.1 — a calibrar'),
  ('liquidez_multiplo_stake', 10,   false, 'maior', 1, 'Doutrina v0.1 — a calibrar'),
  ('snapshot_idade_max_s',    600,  false, 'menor', 1, 'Doutrina v0.1 — a calibrar'),
  ('janela_sincronia_s',      60,   false, 'menor', 1, 'Correção #1 — a calibrar'),
  ('stake_max_pct',           2.0,  true,  'menor', 1, 'Doutrina v0.1 — PÉTREO'),
  ('kelly_fracao',            0.25, true,  'menor', 1, 'Doutrina v0.1 — PÉTREO'),
  ('drawdown_suspensao_pct',  20,   true,  'menor', 1, 'Doutrina v0.1 — PÉTREO'),
  ('amostra_minima',          200,  true,  'maior', 1, 'Doutrina v0.1 — PÉTREO');

insert into casas (nome, tipo, comissao_pct) values
  ('pinnacle',         'referencia', 0),
  ('betfair_exchange', 'exchange',   6.5);
