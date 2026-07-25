-- 0016 — A verdade do evento: identidade única, upsert transacional e regra de
-- remarcação/cancelamento (P1.2).
--
-- PROBLEMA (duas metades, ambas verificadas no código e no banco):
--
--   A) IDENTIDADE. `garantir_evento` era read-then-insert: lê por
--      `ids_externos->>'odds_api'` e, se não achar, insere. Não havia índice
--      NENHUM sobre essa chave (só a definição da coluna, na 0001). Dois ciclos do
--      L0 concorrentes — ou um retry — podem não achar e inserir duas linhas para a
--      MESMA partida. A partir daí os snapshots se dividem entre dois eventos, e
--      nenhuma revisão de referência fica completa: o L1 emite zero.
--
--   B) ADIAMENTO. Nada nunca atualizava o evento existente. Se a fonte mudar o
--      kickoff, o banco continua com o antigo — e `inicio_utc` é a pedra em que
--      TODO o resto se apoia: a trava de apito do L1 (P0.1), a recusa da
--      `fn_registrar_sinal`, a fila do L4, a linha de fechamento, e o
--      `inicio_utc`/`dia` congelados na `exposicoes_papel`. Um kickoff velho não
--      corrompe um desses — corrompe todos ao mesmo tempo, e nenhum tem como
--      perceber: é código correto operando sobre um fato errado.
--
-- SOLUÇÃO: a fonte governa os fatos do evento, e toda mudança é REGISTRADA antes
-- de valer. `fn_garantir_evento` é get-or-create-or-update numa transação só, com
-- a linha travada; o índice único é a garantia final sob concorrência. Cada
-- alteração vira uma linha de `eventos_revisoes` (append-only) com antes/depois.
--
-- REMARCAÇÃO INVALIDA, CORREÇÃO NÃO. Mudança de `inicio_utc` é `remarcado`: o
-- mercado que precificava a partida das 15h não é o mercado da partida das 20h, e
-- comparar a odd de emissão de um com a linha de fechamento do outro mede duas
-- coisas diferentes. Todo item emitido ANTES da revisão deixa de ser comparável e
-- sai da amostra com motivo próprio (`indisponivel_evento_remarcado`); item
-- emitido DEPOIS vale normalmente — a regra é `criado_em <= invalidado_em`, que é
-- verificável linha a linha. O empate conta como INVALIDADO: no instante exato da
-- revisão não dá para dizer de qual mercado veio a emissão, e ambiguidade aborta (P6). Correção de nome de time ou de liga não invalida
-- nada. `eventos.invalidado_em` guarda o carimbo da última revisão invalidante:
-- é cache do que `eventos_revisoes` já diz, para o L4 ler junto com o evento.
--
-- CANCELAMENTO NÃO É INFERIDO DA AUSÊNCIA. A The Odds API simplesmente para de
-- listar um jogo cancelado — e uma resposta parcial por falha de rede é
-- INDISTINGUÍVEL disso. Inferir cancelamento de ausência seria tratar dado
-- ausente como confirmação positiva, exatamente o que P6 proíbe (e o que o P0.4
-- corrigiu no L1). Fica `fn_marcar_evento_cancelado`, explícita; a detecção
-- automática é decisão de rito (PC-CANCELAMENTO), não coisa que eu decida aqui.

-- ---------- 1. Identidade única do evento ----------

-- Verificado antes de criar: 61 eventos, 61 com id da fonte, ZERO duplicados.
-- Parcial: evento sem id da fonte (não deveria existir — `garantir_evento` recusa)
-- não bloqueia o índice.
create unique index ux_eventos_id_externo_odds_api
  on eventos ((ids_externos->>'odds_api'))
  where ids_externos ? 'odds_api';

-- ---------- 2. Marcador de invalidação + status de cancelamento ----------

alter table eventos add column invalidado_em timestamptz;
comment on column eventos.invalidado_em is
  'Carimbo da última revisão que invalida itens emitidos antes dela (remarcação '
  'ou cancelamento). Cache de eventos_revisoes, lido pelo L4 junto com o evento.';

alter table eventos drop constraint eventos_status_check;
alter table eventos add constraint eventos_status_check
  check (status in ('agendado','ao_vivo','encerrado','adiado','cancelado','proibido'));

-- ---------- 3. Histórico de revisões (append-only) ----------

create table eventos_revisoes (
  id         bigint generated always as identity primary key,
  evento_id  uuid not null references eventos(id),
  tipo       text not null check (tipo in ('remarcado','corrigido','cancelado')),
  campos     text[] not null,          -- quais fatos mudaram
  antes      jsonb  not null,
  depois     jsonb  not null,
  fonte      text   not null,          -- quem afirmou a mudança
  motivo     text,
  criado_em  timestamptz not null default now()
);
create index ix_eventos_revisoes_evento on eventos_revisoes (evento_id, criado_em desc);

create trigger tg_eventos_revisoes_del before delete on eventos_revisoes
  for each row execute function fn_bloqueia_delete();
create trigger tg_eventos_revisoes_upd before update on eventos_revisoes
  for each row execute function fn_bloqueia_update();
alter table eventos_revisoes enable row level security;

-- ---------- 4. Upsert transacional ----------

-- Fonte HARDCODED em 'odds_api' de propósito. O índice único é sobre a expressão
-- literal; um parâmetro `p_fonte` não casaria com ele e o caminho de escrita
-- degradaria para varredura sequencial em silêncio. Uma segunda fonte externa
-- (betfair) exige o seu próprio índice e o seu próprio ramo — generalizar agora
-- criaria um caminho não-indexado que ninguém veria quebrar.
create or replace function fn_garantir_evento(p_dados jsonb) returns jsonb
language plpgsql
set search_path = public, pg_temp
as $$
declare
  v_id_ext    text;
  v_inicio    timestamptz;
  v_ev        eventos;
  v_novo      eventos;
  v_campos    text[] := '{}';
  v_tipo      text;
  v_liberadas int := 0;
begin
  v_id_ext := nullif(p_dados->'ids_externos'->>'odds_api', '');
  if v_id_ext is null then
    -- Sem chave estável não se cria evento: inventar identidade é pior que perder
    -- o tick (P6). O L0 já descarta e loga.
    return jsonb_build_object('id', null, 'criado', false, 'motivo', 'sem_id_da_fonte');
  end if;

  v_inicio := nullif(p_dados->>'inicio_utc', '')::timestamptz;
  if v_inicio is null then
    return jsonb_build_object('id', null, 'criado', false, 'motivo', 'sem_inicio_utc');
  end if;

  select * into v_ev from eventos
   where ids_externos->>'odds_api' = v_id_ext
   for update;

  if not found then
    begin
      insert into eventos (esporte, liga, mandante, visitante, inicio_utc, ids_externos)
      values (coalesce(nullif(p_dados->>'esporte',''), 'futebol'),
              coalesce(p_dados->>'liga', ''),
              coalesce(p_dados->>'mandante', ''),
              coalesce(p_dados->>'visitante', ''),
              v_inicio,
              coalesce(p_dados->'ids_externos', '{}'::jsonb))
      returning * into v_ev;
    exception when unique_violation then
      -- Outro processo criou entre o SELECT e o INSERT. É o resultado desejado
      -- (existe UM evento), não erro — o índice único fez o trabalho.
      select * into v_ev from eventos where ids_externos->>'odds_api' = v_id_ext;
      return jsonb_build_object('id', v_ev.id, 'criado', false, 'alterado', false,
                                'motivo', 'criado_por_outro_processo');
    end;
    return jsonb_build_object('id', v_ev.id, 'criado', true, 'alterado', false);
  end if;

  -- Fatos que a fonte governa. Campo ausente no payload não apaga o que existe.
  if v_ev.inicio_utc is distinct from v_inicio then
    v_campos := array_append(v_campos, 'inicio_utc');
  end if;
  if nullif(p_dados->>'mandante','') is not null
     and v_ev.mandante is distinct from p_dados->>'mandante' then
    v_campos := array_append(v_campos, 'mandante');
  end if;
  if nullif(p_dados->>'visitante','') is not null
     and v_ev.visitante is distinct from p_dados->>'visitante' then
    v_campos := array_append(v_campos, 'visitante');
  end if;
  if nullif(p_dados->>'liga','') is not null
     and v_ev.liga is distinct from p_dados->>'liga' then
    v_campos := array_append(v_campos, 'liga');
  end if;

  if array_length(v_campos, 1) is null then
    return jsonb_build_object('id', v_ev.id, 'criado', false, 'alterado', false);
  end if;

  v_tipo := case when 'inicio_utc' = any(v_campos) then 'remarcado' else 'corrigido' end;

  update eventos
     set inicio_utc    = v_inicio,
         mandante      = coalesce(nullif(p_dados->>'mandante',''),  eventos.mandante),
         visitante     = coalesce(nullif(p_dados->>'visitante',''), eventos.visitante),
         liga          = coalesce(nullif(p_dados->>'liga',''),      eventos.liga),
         ids_externos  = eventos.ids_externos || coalesce(p_dados->'ids_externos','{}'::jsonb),
         invalidado_em = case when v_tipo = 'remarcado' then now() else eventos.invalidado_em end
   where eventos.id = v_ev.id
  returning * into v_novo;

  insert into eventos_revisoes (evento_id, tipo, campos, antes, depois, fonte)
  values (v_ev.id, v_tipo, v_campos, to_jsonb(v_ev), to_jsonb(v_novo), 'odds_api');

  -- Remarcação derruba a posição de papel: a oportunidade entregue era para a
  -- partida no horário antigo, e ela deixou de existir.
  if v_tipo = 'remarcado' then
    with baixa as (
      update exposicoes_papel
         set status = 'liberada', motivo_baixa = 'evento remarcado', baixado_em = now()
       where evento_id = v_ev.id and status = 'reservada'
      returning 1
    )
    select count(*) into v_liberadas from baixa;
  end if;

  return jsonb_build_object('id', v_ev.id, 'criado', false, 'alterado', true,
                            'tipo', v_tipo, 'campos', to_jsonb(v_campos),
                            'posicoes_papel_liberadas', v_liberadas);
end $$;

-- ---------- 5. Cancelamento explícito ----------

create or replace function fn_marcar_evento_cancelado(
  p_evento_id uuid, p_motivo text default null, p_fonte text default 'manual'
) returns jsonb
language plpgsql
set search_path = public, pg_temp
as $$
declare v_ev eventos; v_novo eventos; v_liberadas int := 0;
begin
  select * into v_ev from eventos where id = p_evento_id for update;
  if not found then
    return jsonb_build_object('cancelado', false, 'motivo', 'evento_inexistente');
  end if;
  if v_ev.status = 'cancelado' then
    return jsonb_build_object('cancelado', false, 'motivo', 'ja_cancelado');
  end if;

  update eventos
     set status = 'cancelado', invalidado_em = coalesce(eventos.invalidado_em, now())
   where eventos.id = p_evento_id
  returning * into v_novo;

  insert into eventos_revisoes (evento_id, tipo, campos, antes, depois, fonte, motivo)
  values (p_evento_id, 'cancelado', array['status'], to_jsonb(v_ev), to_jsonb(v_novo),
          p_fonte, p_motivo);

  with baixa as (
    update exposicoes_papel
       set status = 'liberada', motivo_baixa = 'evento cancelado', baixado_em = now()
     where evento_id = p_evento_id and status = 'reservada'
    returning 1
  )
  select count(*) into v_liberadas from baixa;

  return jsonb_build_object('cancelado', true, 'posicoes_papel_liberadas', v_liberadas);
end $$;

-- ---------- 6. Desfechos de CLV para item invalidado ----------

alter table clv_resultados drop constraint clv_resultados_resultado_check;
alter table clv_resultados add constraint clv_resultados_resultado_check
  check (resultado in (
    'calculado',
    'indisponivel_sem_referencia',
    'indisponivel_sem_revisao_completa',
    'indisponivel_revisao_defasada',
    'indisponivel_evento_inconsistente',
    'indisponivel_evento_remarcado',    -- emitido antes de a partida ser remarcada
    'indisponivel_evento_cancelado'     -- partida não aconteceu
  ));

-- ---------- 7. A fila do L4 enxerga o evento invalidado NA HORA ----------

-- Sem isto um evento remarcado para depois só voltaria à fila no NOVO kickoff, e
-- aí o L4 calcularia CLV comparando a odd de emissão do mercado antigo com a linha
-- de fechamento do mercado novo. Um evento CANCELADO nunca mais entraria na fila —
-- seus itens ficariam pendentes para sempre, que é a starvation que a 0007 corrigiu.
--
-- A condição de tempo é POR ITEM, não por evento: um evento remarcado entra na fila
-- imediatamente por causa dos itens ANTERIORES à revisão, mas os emitidos DEPOIS
-- dela continuam esperando o novo kickoff. Aplicar a condição ao evento inteiro
-- reintroduziria o encerramento prematuro que a 0007 corrigiu.
create or replace function fn_fila_fechamento(
  p_agora timestamptz, p_assentamento_s numeric, p_limite int default 200
) returns setof eventos
language sql
stable
set search_path = public, pg_temp
as $$
  select e.*
  from eventos e
  where not exists (select 1 from clv_eventos_finalizados f where f.evento_id = e.id)
    and (
      exists (
        select 1 from sinais s
         where s.evento_id = e.id
           and s.status in ('confirmado','vetado','expirado','erro','timeout_crivo')
           and not exists (select 1 from clv_resultados r where r.sinal_id = s.id)
           and (e.inicio_utc + (p_assentamento_s || ' seconds')::interval <= p_agora
                or (e.invalidado_em is not null and s.criado_em <= e.invalidado_em))
      )
      or exists (
        select 1 from abortos_l1 a
         where a.evento_id = e.id and a.clv_rastrear
           and not exists (select 1 from clv_resultados r where r.aborto_l1_id = a.id)
           and (e.inicio_utc + (p_assentamento_s || ' seconds')::interval <= p_agora
                or (e.invalidado_em is not null and a.ts <= e.invalidado_em))
      )
    )
  order by e.inicio_utc
  limit p_limite
$$;

-- ---------- 8. Segurança (mesmo desenho da 0002) ----------

revoke all on function fn_garantir_evento(jsonb)                          from public, anon, authenticated;
revoke all on function fn_marcar_evento_cancelado(uuid, text, text)       from public, anon, authenticated;
grant execute on function fn_garantir_evento(jsonb)                       to service_role;
grant execute on function fn_marcar_evento_cancelado(uuid, text, text)    to service_role;
