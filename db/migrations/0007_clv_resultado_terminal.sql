-- 0007 — Unidade terminal de fechamento POR ITEM (achado #4 do 2º ciclo; Sugestão nº 10).
--
-- PROBLEMA: o L4 usava `eventos.status` como marcador de conclusão do CLV. Esse
-- modelo não representa sucesso parcial, indisponibilidade permanente nem atraso
-- operacional — e produzia três defeitos ao mesmo tempo:
--   A) encerramento prematuro: `marcar_evento_encerrado` era incondicional, então um
--      evento com book de 1x2 mas sem book de `ou` encerrava e os sinais de `ou`
--      nunca recebiam CLV — perda permanente e silenciosa;
--   B) retry infinito: sem NENHUM book, a função retornava sem encerrar, e o evento
--      voltava à fila a cada ciclo, para sempre;
--   C) itens fora do recorte: sinais em `aguardando_crivo`/`expirado`/`erro` no
--      kickoff jamais recebiam desfecho e o encerramento passava por cima deles.
--   Como a fila tem `limit 200` por `inicio_utc`, os travados de (B) acumulavam e
--   podiam expulsar eventos novos — starvation do fechamento.
--
-- SOLUÇÃO: a unidade terminal passa a ser o ITEM (sinal / aborto rastreado), não o
-- evento. Cada item termina EXATAMENTE UMA VEZ, como `calculado` ou como uma
-- indisponibilidade nomeada. O evento só é finalizado quando todos os seus itens têm
-- desfecho — e essa verificação é feita PELO BANCO, não pelo app.
--
-- NOTA DE ADEQUAÇÃO AO IMPLANTADO: o desenho proposto previa um terceiro tipo de
-- origem, `candidatos_sombra(id)`. Essa tabela NÃO existe: pelo achado 8, o
-- candidato_sombra é uma linha de `abortos_l1` (gate_reprovado='mercado_nao_homologado',
-- clv_rastrear=true). Logo a origem é `aborto_l1_id`, e o que o distingue do near-miss
-- é a `categoria` ('calibracao' vs 'contrafactual_l1'). Criar uma tabela paralela só
-- para o FK duplicaria a identidade do mesmo registro.

-- ---------- 1. Novo status de sinal: timeout_crivo ----------

-- `aguardando_crivo` não pode sobreviver ao kickoff: o L2 não confirma uma aposta
-- depois de a partida começar. O item vira `timeout_crivo` — status próprio, não
-- `expirado`: os motivos são diferentes (expirado = preço deixou de ser executável;
-- timeout_crivo = a análise não concluiu a tempo) e confundi-los apagaria a
-- distinção entre falha de mercado e falha de pipeline.
-- O trigger `fn_sinais_update` (0001) só permite transição A PARTIR de
-- 'aguardando_crivo', então esta transição é alcançável sem tocar o trigger.
alter table sinais drop constraint sinais_status_check;
alter table sinais add constraint sinais_status_check
  check (status in ('aguardando_crivo','confirmado','vetado','expirado','erro','timeout_crivo'));

-- ---------- 2. Resultado terminal por item ----------

create table clv_resultados (
  id                  bigint generated always as identity primary key,
  evento_id           uuid not null references eventos(id),

  -- Origem: exatamente uma. (candidato_sombra é um aborto_l1 — ver nota acima.)
  sinal_id            uuid   references sinais(id),
  aborto_l1_id        bigint references abortos_l1(id),

  -- Separa CLV real, decisório (L2), do L1, operacional e de calibração: misturá-los
  -- na mesma média responderia a pergunta errada.
  categoria           text not null check (categoria in (
                        'real',                      -- sinal confirmado
                        'contrafactual_l2',          -- vetado pelo crivo
                        'contrafactual_l1',          -- near-miss abortado no L1
                        'contrafactual_operacional', -- expirado / erro / timeout_crivo
                        'calibracao')),              -- candidato_sombra (mercado não homologado)

  resultado           text not null check (resultado in (
                        'calculado',
                        'indisponivel_sem_referencia',
                        'indisponivel_sem_revisao_completa',
                        'indisponivel_revisao_defasada',
                        'indisponivel_evento_inconsistente')),
  motivo              text,

  mercado             text not null,
  selecao             text not null,
  linha               numeric,

  ts_fechamento       timestamptz,     -- ts_fonte da revisão (quando houve alguma)
  idade_fechamento_s  numeric,         -- início − ts_fonte
  clv_log_id          bigint references clv_log(id),

  criado_em           timestamptz not null default now(),

  check (num_nonnulls(sinal_id, aborto_l1_id) = 1),
  -- 'calculado' exige a medição; indisponível não pode apontar para uma.
  check ((resultado = 'calculado') = (clv_log_id is not null))
);

-- Uma origem gera EXATAMENTE UM resultado terminal (idempotência sob concorrência).
create unique index ux_clv_resultado_sinal
  on clv_resultados (sinal_id) where sinal_id is not null;
create unique index ux_clv_resultado_aborto
  on clv_resultados (aborto_l1_id) where aborto_l1_id is not null;
create index ix_clv_resultado_evento on clv_resultados (evento_id);

-- ---------- 3. Marcador de conclusão do L4 (separado de eventos.status) ----------

-- `eventos.status` responde "a partida saiu do estado agendado?"; isto responde
-- "todos os itens auditáveis deste evento já têm desfecho de CLV?". São perguntas
-- diferentes e não devem compartilhar uma coluna.
create table clv_eventos_finalizados (
  evento_id      uuid primary key references eventos(id),
  total_itens    int not null,
  calculados     int not null,
  indisponiveis  int not null,
  finalizado_em  timestamptz not null default now(),
  detalhe        jsonb not null default '{}'
);

-- ---------- 4. Append-only + RLS (mesmo desenho do 0001/0002) ----------

do $$
declare t text;
begin
  foreach t in array array['clv_resultados','clv_eventos_finalizados']
  loop
    execute format('create trigger tg_%s_del before delete on %s
                    for each row execute function fn_bloqueia_delete()', t, t);
    execute format('create trigger tg_%s_upd before update on %s
                    for each row execute function fn_bloqueia_update()', t, t);
    execute format('alter table %s enable row level security', t);
  end loop;
end $$;

-- ---------- 5. Registro atômico do desfecho ----------

-- `calculado` grava a medição em `clv_log` E o desfecho em `clv_resultados` na MESMA
-- transação: uma medição sem desfecho (ou vice-versa) reabriria exatamente o buraco
-- que este item fecha. Indisponível grava só o desfecho.
create or replace function fn_registrar_resultado_clv(
  p_evento_id           uuid,
  p_sinal_id            uuid,
  p_aborto_id           bigint,
  p_categoria           text,
  p_resultado           text,
  p_mercado             text,
  p_selecao             text,
  p_linha               numeric   default null,
  p_motivo              text      default null,
  p_ts_fechamento       timestamptz default null,
  p_idade_s             numeric   default null,
  p_contrafactual       boolean   default false,
  p_odd_emissao         numeric   default null,
  p_odd_fechamento_ref  numeric   default null,
  p_p_emissao           numeric   default null,
  p_p_fechamento        numeric   default null,
  p_clv_pct             numeric   default null
) returns jsonb
language plpgsql
set search_path = public, pg_temp
as $$
declare
  v_clv_id bigint := null;
begin
  if p_resultado = 'calculado' then
    insert into clv_log (sinal_id, aborto_l1_id, contrafactual, odd_emissao,
                         odd_fechamento_ref, p_emissao, p_fechamento, clv_pct,
                         ts_fechamento)
    values (p_sinal_id, p_aborto_id, p_contrafactual, p_odd_emissao,
            p_odd_fechamento_ref, p_p_emissao, p_p_fechamento, p_clv_pct,
            p_ts_fechamento)
    returning id into v_clv_id;
  end if;

  insert into clv_resultados (evento_id, sinal_id, aborto_l1_id, categoria, resultado,
                              motivo, mercado, selecao, linha, ts_fechamento,
                              idade_fechamento_s, clv_log_id)
  values (p_evento_id, p_sinal_id, p_aborto_id, p_categoria, p_resultado,
          p_motivo, p_mercado, p_selecao, p_linha, p_ts_fechamento,
          p_idade_s, v_clv_id);

  return jsonb_build_object('registrado', true, 'clv_log_id', v_clv_id);
exception when unique_violation then
  -- Corrida entre processos do L4: o item já tem desfecho. O bloco inteiro é
  -- desfeito (subtransação), então não sobra `clv_log` órfão.
  return jsonb_build_object('registrado', false, 'motivo', 'ja_registrado');
end $$;

-- ---------- 6. Finalização do evento, verificada pelo banco ----------

-- O app NÃO decide sozinho que acabou: a função recusa finalizar enquanto existir
-- item esperado sem desfecho. Itens esperados = sinais que já saíram de
-- 'aguardando_crivo' + abortos rastreados (inclui os candidatos_sombra).
create or replace function fn_finalizar_evento_clv(
  p_evento_id uuid, p_detalhe jsonb default '{}'::jsonb
) returns jsonb
language plpgsql
set search_path = public, pg_temp
as $$
declare
  v_pendentes int;
  v_total int;
  v_calc int;
begin
  select count(*) into v_pendentes from (
    select s.id::text as ref from sinais s
      where s.evento_id = p_evento_id
        and s.status in ('confirmado','vetado','expirado','erro','timeout_crivo')
        and not exists (select 1 from clv_resultados r where r.sinal_id = s.id)
    union all
    select a.id::text from abortos_l1 a
      where a.evento_id = p_evento_id and a.clv_rastrear
        and not exists (select 1 from clv_resultados r where r.aborto_l1_id = a.id)
  ) t;

  if v_pendentes > 0 then
    raise exception 'evento % tem % item(ns) de CLV sem desfecho — não finaliza',
                    p_evento_id, v_pendentes;
  end if;

  select count(*), count(*) filter (where resultado = 'calculado')
    into v_total, v_calc
    from clv_resultados where evento_id = p_evento_id;

  insert into clv_eventos_finalizados (evento_id, total_itens, calculados,
                                       indisponiveis, detalhe)
  values (p_evento_id, v_total, v_calc, v_total - v_calc, p_detalhe)
  on conflict (evento_id) do nothing;

  return jsonb_build_object('finalizado', true, 'total', v_total,
                            'calculados', v_calc, 'indisponiveis', v_total - v_calc);
end $$;

-- ---------- 7. Fila do L4 ----------

-- Substitui "todo evento iniciado com status <> encerrado". Agora: eventos que ainda
-- têm item sem desfecho E cujo kickoff + assentamento já passou. Evento sem sinal,
-- sem aborto rastreado e sem candidato_sombra NÃO entra na fila — é o que elimina o
-- starvation causado por partidas irrelevantes ocupando o `limit`.
create or replace function fn_fila_fechamento(
  p_agora timestamptz, p_assentamento_s numeric, p_limite int default 200
) returns setof eventos
language sql
stable
set search_path = public, pg_temp
as $$
  select e.*
  from eventos e
  where e.inicio_utc + (p_assentamento_s || ' seconds')::interval <= p_agora
    and not exists (select 1 from clv_eventos_finalizados f where f.evento_id = e.id)
    and (
      exists (select 1 from sinais s
               where s.evento_id = e.id
                 and s.status in ('confirmado','vetado','expirado','erro','timeout_crivo')
                 and not exists (select 1 from clv_resultados r where r.sinal_id = s.id))
      or exists (select 1 from abortos_l1 a
                  where a.evento_id = e.id and a.clv_rastrear
                    and not exists (select 1 from clv_resultados r where r.aborto_l1_id = a.id))
    )
  order by e.inicio_utc
  limit p_limite
$$;

-- ---------- 8. Sinais que não podem sobreviver ao kickoff ----------

-- `aguardando_crivo` após o início vira `timeout_crivo` (a análise não concluiu a
-- tempo). Sem isto o item nunca teria desfecho e o evento nunca finalizaria.
create or replace function fn_timeout_crivo(p_agora timestamptz)
returns int
language plpgsql
set search_path = public, pg_temp
as $$
declare v_n int;
begin
  with alvo as (
    select s.id from sinais s join eventos e on e.id = s.evento_id
     where s.status = 'aguardando_crivo' and e.inicio_utc <= p_agora
  )
  update sinais set status = 'timeout_crivo'
   where id in (select id from alvo);
  get diagnostics v_n = row_count;
  return v_n;
end $$;

-- ---------- 9. Superfície de execução (postura da 0002) ----------

revoke all on function fn_registrar_resultado_clv(uuid, uuid, bigint, text, text, text, text, numeric, text, timestamptz, numeric, boolean, numeric, numeric, numeric, numeric, numeric) from public, anon, authenticated;
revoke all on function fn_finalizar_evento_clv(uuid, jsonb) from public, anon, authenticated;
revoke all on function fn_fila_fechamento(timestamptz, numeric, int) from public, anon, authenticated;
revoke all on function fn_timeout_crivo(timestamptz) from public, anon, authenticated;
grant execute on function fn_registrar_resultado_clv(uuid, uuid, bigint, text, text, text, text, numeric, text, timestamptz, numeric, boolean, numeric, numeric, numeric, numeric, numeric) to service_role;
grant execute on function fn_finalizar_evento_clv(uuid, jsonb) to service_role;
grant execute on function fn_fila_fechamento(timestamptz, numeric, int) to service_role;
grant execute on function fn_timeout_crivo(timestamptz) to service_role;

-- ---------- 10. Gate de assentamento (Sugestão nº 10) ----------

-- Quanto esperar DEPOIS do kickoff para que os snapshots já capturados cheguem ao
-- banco. Contrato distinto de `fechamento_idade_max_s` (quão distante do kickoff o
-- book usado pode estar). A auditoria mediu atraso de persistência de ~50 s em média,
-- com extremos de minutos; 600 s dá margem segura. A calibrar.
insert into gates (nome, valor, petreo, direcao_endurecer, versao, motivo) values
  ('fechamento_assentamento_s', 600, false, 'maior', 1,
   'Sugestão nº 10 — espera após o kickoff para a persistência assentar (L4) — a calibrar');
