-- 0015 — Exposição de papel: os tetos de exposição voltam a existir no modo sombra (P0.8).
--
-- PROBLEMA (medido no banco vivo em 25/07/2026): `vw_exposicao_aberta` deriva
-- EXCLUSIVAMENTE de `apostas` com `resultado='pendente'`. No regime de papel não há
-- aposta nenhuma — `apostas` tem 0 linhas —, logo a view devolve vazio e
-- `_exposto_do_evento` monta {jogo:0, liga_dia:0, dia:0} para todo grupo. Os três
-- gates de exposição (`exposicao_max_jogo_pct` 3%, `exposicao_max_liga_dia_pct` 6%,
-- `exposicao_max_dia_pct` 10%) comparam `0 + stake > teto` e nunca reprovam.
-- Consequência: dez oportunidades no MESMO jogo passam, e o sistema medido no paper
-- trading não é o sistema que rodaria com dinheiro — a diferença aparece justamente
-- na dimensão que quebra banca (concentração), não na que se mede por CLV.
--
-- SOLUÇÃO: uma POSIÇÃO DE PAPEL (`exposicoes_papel`) espelhando `apostas`. A
-- oportunidade ENTREGUE (cartão que o L3 confirmou no Telegram — o análogo exato de
-- "o Daniel apostou") reserva o stake nocional; a posição é baixada quando o L4
-- finaliza o evento (o análogo da liquidação), quando uma aposta real a substitui,
-- ou por liberação explícita. Os gates passam a ler `vw_exposicao_total`, que soma
-- as fontes conforme o regime.
--
-- TRÊS FONTES, não duas. Real e papel não bastam: entre a EMISSÃO do sinal e a
-- ENTREGA do cartão passam um ciclo do L2 e um do L3. Nessa janela a exposição é
-- invisível, e um segundo sinal no mesmo jogo passa pelo teto sem obstáculo — o
-- buraco original inteiro, só que estreito. A terceira fonte ('em_voo') conta o
-- sinal já emitido e ainda sem desfecho como exposição comprometida, e some assim
-- que ele vira posição (papel ou real) ou morre. Isso vale para os DOIS regimes:
-- com dinheiro, um `confirmado` que o Daniel ainda não apostou também é compromisso.
--
-- ONDE ISTO SE AFASTA DA ESPECIFICAÇÃO: foi pedido que a reserva durasse "até o
-- evento começar; a oportunidade expirar; ocorrer liquidação de papel". As duas
-- primeiras condições tornariam a terceira inalcançável (nada sobrevive ao apito
-- para ser liquidado). Adotou-se a leitura coerente com o dinheiro real, onde a
-- posição vive até LIQUIDAR: a baixa automática é `fn_finalizar_evento_clv`, que
-- roda `fechamento_assentamento_s` (600 s) depois do apito. Fica registrado o
-- resíduo: uma posição de papel vive da entrega até o apito+10 min, enquanto a
-- aposta real vive até o fim da partida — o teto por liga/dia é, portanto, um pouco
-- mais frouxo no papel do que seria com dinheiro. Fechar essa diferença exige feed
-- de resultado de partida, que o sistema não tem, e é decisão de rito.
--
-- O nocional absoluto vem do DOSSIÊ (`exposicao.stake_valor`, gravado na emissão),
-- nunca recalculado: `sinais.stake_pct` é fração da banca, e recompor o valor aqui
-- multiplicaria por uma banca que pode não ser mais a mesma.

-- ---------- 1. A posição de papel ----------

create table exposicoes_papel (
  id            bigint generated always as identity primary key,
  sinal_id      uuid not null references sinais(id),
  evento_id     uuid not null references eventos(id),

  -- Chaves de agregação congeladas na reserva (o teto é por jogo / liga-dia / dia).
  -- `dia` é a data da PARTIDA em UTC — não a data do registro: o teto diário limita
  -- concentração em rodada, e é assim que o L1 monta a chave que consulta.
  liga          text not null,
  dia           date not null,
  inicio_utc    timestamptz not null,

  stake_valor   numeric not null check (stake_valor > 0),
  odd_emissao   numeric not null check (odd_emissao > 1),
  banca_valor   numeric not null check (banca_valor > 0),
  banca_origem  text    not null check (banca_origem = 'papel'),

  status        text not null default 'reservada'
                check (status in ('reservada',      -- posição aberta: consome teto
                                  'liquidada',      -- L4 finalizou o evento
                                  'substituida',    -- virou aposta REAL (evita dupla contagem)
                                  'liberada')),     -- baixa explícita (não deveria ter reservado)
  motivo_baixa  text,
  reservado_em  timestamptz not null default now(),
  baixado_em    timestamptz,

  -- Baixa e carimbo andam juntos: posição baixada sem quando/por quê é buraco de auditoria.
  check ((status = 'reservada') = (baixado_em is null))
);

-- Uma oportunidade reserva UMA vez, ainda que o L3 reenvie o cartão (a janela entre
-- "o Telegram aceitou" e "o banco marcou entregue" é irredutível — ver l3/notifica).
create unique index ux_exposicoes_papel_sinal on exposicoes_papel (sinal_id);
create index ix_exposicoes_papel_abertas
  on exposicoes_papel (dia, liga, evento_id) where status = 'reservada';
create index ix_exposicoes_papel_evento on exposicoes_papel (evento_id);

-- ---------- 2. Append-only com baixa única (mesmo desenho de `apostas`) ----------

create or replace function fn_exposicoes_papel_update() returns trigger
language plpgsql
set search_path = public, pg_temp
as $$
begin
  if old.status <> 'reservada' or new.status = 'reservada'
     or (to_jsonb(new) - array['status','motivo_baixa','baixado_em'])
        <> (to_jsonb(old) - array['status','motivo_baixa','baixado_em']) then
    raise exception 'exposicoes_papel: apenas baixa única (reservada → liquidada|substituida|liberada)';
  end if;
  return new;
end $$;

create trigger tg_exposicoes_papel_upd before update on exposicoes_papel
  for each row execute function fn_exposicoes_papel_update();
create trigger tg_exposicoes_papel_del before delete on exposicoes_papel
  for each row execute function fn_bloqueia_delete();
alter table exposicoes_papel enable row level security;

-- ---------- 3. Reserva (chamada pelo L3 na ENTREGA confirmada do cartão) ----------

create or replace function fn_reservar_exposicao_papel(
  p_sinal_id uuid,
  p_agora    timestamptz default now()
) returns jsonb
language plpgsql
set search_path = public, pg_temp
as $$
declare
  v_sinal  sinais;
  v_ev     eventos;
  v_stake  numeric;
  v_banca  numeric;
  v_linha  exposicoes_papel;
begin
  select * into v_sinal from sinais where id = p_sinal_id;
  if not found then
    return jsonb_build_object('reservado', false, 'motivo', 'sinal_inexistente');
  end if;

  -- Regime REAL não reserva papel: a aposta do Daniel é a posição. Reservar aqui
  -- contaria a mesma oportunidade duas vezes.
  if coalesce(v_sinal.dossie->>'banca_origem', 'real') <> 'papel' then
    return jsonb_build_object('reservado', false, 'motivo', 'regime_real');
  end if;

  select * into v_ev from eventos where id = v_sinal.evento_id for update;
  if not found then
    return jsonb_build_object('reservado', false, 'motivo', 'evento_inexistente');
  end if;
  -- Cartão entregue DEPOIS do apito não é oportunidade (P0.7: `confirmado_nao_entregue`)
  -- e não pode consumir teto de uma partida que já começou.
  if v_ev.inicio_utc <= p_agora then
    return jsonb_build_object('reservado', false, 'motivo', 'apito_ja_soou');
  end if;

  v_stake := nullif(v_sinal.dossie->'exposicao'->>'stake_valor', '')::numeric;
  v_banca := nullif(v_sinal.dossie->'exposicao'->>'banca_valor', '')::numeric;
  if v_stake is null or v_stake <= 0 or v_banca is null or v_banca <= 0 then
    -- Fail-loud: sem nocional gravado a posição seria fantasma. Não se inventa valor.
    return jsonb_build_object('reservado', false, 'motivo', 'nocional_ausente_no_dossie');
  end if;

  begin
    insert into exposicoes_papel (sinal_id, evento_id, liga, dia, inicio_utc,
                                  stake_valor, odd_emissao, banca_valor, banca_origem)
    values (p_sinal_id, v_ev.id, coalesce(v_ev.liga, ''),
            (v_ev.inicio_utc at time zone 'UTC')::date, v_ev.inicio_utc,
            round(v_stake, 2), v_sinal.odd_venue, v_banca, 'papel')
    returning * into v_linha;
  exception when unique_violation then
    return jsonb_build_object('reservado', false, 'motivo', 'ja_reservado');
  end;

  return jsonb_build_object('reservado', true, 'posicao', to_jsonb(v_linha));
end $$;

-- ---------- 4. Baixa ----------

create or replace function fn_baixar_exposicao_papel(
  p_sinal_id uuid,
  p_status   text,
  p_motivo   text        default null,
  p_agora    timestamptz default now()
) returns jsonb
language plpgsql
set search_path = public, pg_temp
as $$
declare v_linha exposicoes_papel;
begin
  if p_status not in ('liquidada','substituida','liberada') then
    raise exception 'baixa inválida de exposicoes_papel: %', p_status;
  end if;
  update exposicoes_papel
     set status = p_status, motivo_baixa = p_motivo, baixado_em = p_agora
   where sinal_id = p_sinal_id and status = 'reservada'
  returning * into v_linha;
  if not found then
    return jsonb_build_object('baixado', false, 'motivo', 'sem_posicao_aberta');
  end if;
  return jsonb_build_object('baixado', true, 'posicao', to_jsonb(v_linha));
end $$;

-- ---------- 5. Aposta real SUBSTITUI a posição de papel ----------

-- Idêntica à 0004, com um único acréscimo: baixa a posição de papel do mesmo sinal.
-- Sem isto, um sinal do regime de papel que virasse aposta real contaria nos dois
-- lados da soma.
create or replace function fn_registrar_aposta(
  p_sinal_id uuid,
  p_casa_id  uuid,
  p_odd      numeric,
  p_stake    numeric
) returns jsonb
language plpgsql
set search_path = public, pg_temp
as $$
declare
  v_aposta apostas;
  v_stake  numeric;
  v_saldo  numeric;
begin
  if p_stake is null or p_stake <= 0 then
    raise exception 'stake deve ser > 0 (recebido: %)', p_stake;
  end if;
  if p_odd is null or p_odd <= 1 then
    raise exception 'odd deve ser > 1 (recebida: %)', p_odd;
  end if;

  perform pg_advisory_xact_lock(hashtext('banca_ledger'));

  v_stake := round(p_stake, 2);
  select coalesce((select saldo_apos from banca_ledger order by id desc limit 1), 0)
    into v_saldo;

  insert into apostas (sinal_id, casa_id, odd_executada, stake_valor)
  values (p_sinal_id, p_casa_id, p_odd, v_stake)
  returning * into v_aposta;

  insert into banca_ledger (tipo, valor, aposta_id, motivo, saldo_apos)
  values ('aposta', -v_stake, v_aposta.id,
          'execução do sinal ' || p_sinal_id::text, v_saldo - v_stake);

  -- Na MESMA transação: a posição de papel deixa de existir como exposição.
  update exposicoes_papel
     set status = 'substituida', motivo_baixa = 'aposta real registrada', baixado_em = now()
   where sinal_id = p_sinal_id and status = 'reservada';

  return jsonb_build_object(
    'aposta', to_jsonb(v_aposta),
    'saldo',  v_saldo - v_stake
  );
end $$;

-- ---------- 6. Finalização do evento LIQUIDA as posições de papel ----------

-- Idêntica à 0007, com o acréscimo da baixa. É aqui — e só aqui — que a posição de
-- papel morre por decurso: `fn_fila_fechamento` só entrega o evento depois de
-- `fechamento_assentamento_s` do apito, então nenhuma posição sobrevive à partida.
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
  v_baixadas int;
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

  with baixa as (
    update exposicoes_papel
       set status = 'liquidada', motivo_baixa = 'evento finalizado no L4', baixado_em = now()
     where evento_id = p_evento_id and status = 'reservada'
    returning 1
  )
  select count(*) into v_baixadas from baixa;

  return jsonb_build_object('finalizado', true, 'total', v_total,
                            'calculados', v_calc, 'indisponiveis', v_total - v_calc,
                            'posicoes_papel_liquidadas', v_baixadas);
end $$;

-- ---------- 7. A exposição que os gates enxergam ----------

-- Mesma forma de `vw_exposicao_aberta` (grouping sets jogo / liga-dia / dia), com as
-- três fontes somadas em `exposto` e discriminadas em colunas próprias — sem a
-- discriminação, um teto reprovado no papel seria indistinguível de um reprovado com
-- dinheiro, e a auditoria do paper trading perderia a informação que importa.
--
-- Correção embutida: a view antiga agrupava por `date(a.executada_em)` — a data em que
-- a aposta foi REGISTRADA — enquanto o L1 consulta pela data da PARTIDA. Uma aposta
-- feita na véspera era arquivada num dia que ninguém consultava, e o teto diário não
-- via. Aqui as três fontes usam a data da partida.
create view vw_exposicao_total as
with posicoes as (
  -- (1) DINHEIRO REAL em risco: vive até a liquidação.
  select s.evento_id,
         coalesce(e.liga, '') as liga,
         (e.inicio_utc at time zone 'UTC')::date as dia,
         a.stake_valor as valor,
         'real'::text as fonte
    from apostas a
    join sinais  s on s.id = a.sinal_id
    join eventos e on e.id = s.evento_id
   where a.resultado = 'pendente'

  union all

  -- (2) POSIÇÃO DE PAPEL: oportunidade entregue no regime de papel.
  select x.evento_id, x.liga, x.dia, x.stake_valor, 'papel'
    from exposicoes_papel x
   where x.status = 'reservada'

  union all

  -- (3) EM VOO: sinal emitido, ainda sem desfecho e ainda sem posição. Sai da conta
  --     assim que vira posição (papel/real), assim que morre (vetado/expirado/erro/
  --     timeout), assim que o cartão é suprimido, ou no apito.
  select s.evento_id,
         coalesce(e.liga, ''),
         (e.inicio_utc at time zone 'UTC')::date,
         round((nullif(s.dossie->'exposicao'->>'stake_valor', ''))::numeric, 2),
         'em_voo'
    from sinais s
    join eventos e on e.id = s.evento_id
   where s.status in ('aguardando_crivo', 'confirmado')
     and e.inicio_utc > now()
     and nullif(s.dossie->'exposicao'->>'stake_valor', '') is not null
     and not exists (select 1 from apostas a          where a.sinal_id = s.id)
     and not exists (select 1 from exposicoes_papel x where x.sinal_id = s.id)
     and not exists (select 1 from notificacoes n
                      where n.sinal_id = s.id and n.tipo = 'administrativo'
                        and n.conteudo like '[expirado-no-envio]%')
)
select evento_id, liga, dia,
       sum(valor)                                                as exposto,
       coalesce(sum(valor) filter (where fonte = 'real'),   0)    as exposto_real,
       coalesce(sum(valor) filter (where fonte = 'papel'),  0)    as exposto_papel,
       coalesce(sum(valor) filter (where fonte = 'em_voo'), 0)    as exposto_em_voo
  from posicoes
 group by grouping sets ((evento_id, liga, dia), (liga, dia), (dia));

-- ---------- 8. Segurança (mesmo desenho da 0002) ----------

alter view public.vw_exposicao_total set (security_invoker = on);
revoke all on public.vw_exposicao_total from anon, authenticated;

revoke all on function fn_reservar_exposicao_papel(uuid, timestamptz)          from public, anon, authenticated;
revoke all on function fn_baixar_exposicao_papel(uuid, text, text, timestamptz) from public, anon, authenticated;
grant execute on function fn_reservar_exposicao_papel(uuid, timestamptz)          to service_role;
grant execute on function fn_baixar_exposicao_papel(uuid, text, text, timestamptz) to service_role;
