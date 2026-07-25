-- 0018 — A entrega ganha desfecho, o alerta ganha episódio, a outbox ganha desistência.
-- (auditoria P1: itens P1.3, P1.4 e P1.6 — três buracos da mesma máquina de estados)
--
-- P1.3 — SUPRESSÃO ESCREVIA UMA LINHA POR CICLO. `enfileirar_cartoes` só checava
--   `notificacoes` do tipo 'sinal'. A supressão gravava tipo 'administrativo', que o
--   `ux_notificacao_cartao` não cobre (é parcial em tipo='sinal'), então no ciclo
--   seguinte nada indicava "já tratei este" e outra linha nascia. Com o L3 a cada 30 s
--   e um sinal confirmado horas antes do apito, são centenas de linhas por sinal.
--
--   E havia um acoplamento pior, não citado na auditoria: o P0.7 decide a categoria do
--   CLV procurando uma notificação administrativa com "[expirado-no-envio]". Parar de
--   escrever essas linhas SEM substituir a fonte reclassificaria todo sinal suprimido
--   como `confirmado_nao_entregue` — apagando a distinção entre perda de MERCADO e
--   perda OPERACIONAL, que é a razão de existir do P0.7. Por isso a correção não é
--   "escrever menos": é ter UM desfecho de entrega por sinal, e o L4 passa a lê-lo.
--
--   SUPRESSÃO NÃO É TERMINAL NA HORA. O preço pode voltar acima da mínima antes do
--   apito, e o cartão sair. O que se acumula é TENTATIVA (contador + último motivo);
--   o desfecho só é selado quando não há mais volta: na entrega, ou no apito.
--
-- P1.4 — ALERTA DE DRAWDOWN A CADA CICLO. O anti-spam olhava só notificações ainda
--   PENDENTES. Depois de entregue, a linha sai de pendente/enviando e o próximo ciclo
--   enfileira outro alerta. Como o kill switch fica ligado até revisão formal (§7),
--   isso é um alerta por ciclo do L3, indefinidamente. A outbox (0010) piorou: antes
--   `entregue` era booleano e a linha continuava visível. Agora a unidade é o
--   EPISÓDIO de suspensão — que a Seção 7 precisaria registrar de qualquer forma.
--
-- P1.6 — OUTBOX SEM DESISTÊNCIA. `fn_devolver_notificacao` devolvia a `pendente` na
--   hora. `tentativas` era incrementado e nunca lido como limite. Token revogado ou
--   chat inválido giram para sempre, o log cresce e mensagens novas disputam a fila
--   com uma que nunca vai sair. Agora há backoff exponencial, teto de tentativas e
--   status `morta` — e cartão morto vira desfecho de entrega `nao_entregue_falha`,
--   que é exatamente o que o P0.7 chama de perda operacional.

-- ================= P1.3 — desfecho de entrega, um por sinal =================

create table entregas_sinal (
  sinal_id             uuid primary key references sinais(id),
  -- NULL enquanto ainda pode mudar. Preenchido = terminal, e não volta atrás.
  desfecho             text check (desfecho in (
                         'entregue',              -- cartão confirmado pelo Telegram
                         'suprimido_preco',       -- odd caiu abaixo da mínima até o apito
                         'suprimido_frescor',     -- sem preço atual até o apito
                         'nao_entregue_timeout',  -- apito chegou antes de qualquer envio
                         'nao_entregue_falha')),  -- a outbox desistiu (dead-letter)
  entregue_em          timestamptz,
  tentativas           int not null default 0,    -- ciclos em que o cartão não pôde sair
  ultimo_motivo        text,                      -- por que não saiu no último ciclo
  ultima_tentativa_em  timestamptz,
  criado_em            timestamptz not null default now(),
  atualizado_em        timestamptz not null default now(),
  check ((desfecho = 'entregue') = (entregue_em is not null))
);
create index ix_entregas_pendentes on entregas_sinal (sinal_id) where desfecho is null;

-- Linha de ESTADO, não de log: muda enquanto o desfecho não existe. O desfecho, esse,
-- é escrito uma vez só — reescrevê-lo apagaria a razão pela qual a oportunidade não
-- chegou, que é o dado que o P0.7 mede.
create or replace function fn_entregas_sinal_update() returns trigger
language plpgsql
set search_path = public, pg_temp
as $$
begin
  if old.desfecho is not null and new.desfecho is distinct from old.desfecho then
    raise exception 'entregas_sinal: desfecho é terminal (% → %)', old.desfecho, new.desfecho;
  end if;
  new.atualizado_em := now();
  return new;
end $$;

create trigger tg_entregas_sinal_upd before update on entregas_sinal
  for each row execute function fn_entregas_sinal_update();
create trigger tg_entregas_sinal_del before delete on entregas_sinal
  for each row execute function fn_bloqueia_delete();
alter table entregas_sinal enable row level security;

-- Uma tentativa frustrada: conta e nomeia, NÃO sela. O preço pode voltar.
create or replace function fn_registrar_tentativa_cartao(
  p_sinal_id uuid, p_motivo text, p_agora timestamptz default now()
) returns jsonb
language plpgsql
set search_path = public, pg_temp
as $$
declare v_linha entregas_sinal;
begin
  insert into entregas_sinal (sinal_id, tentativas, ultimo_motivo, ultima_tentativa_em)
  values (p_sinal_id, 1, p_motivo, p_agora)
  on conflict (sinal_id) do update
    set tentativas = entregas_sinal.tentativas + 1,
        ultimo_motivo = excluded.ultimo_motivo,
        ultima_tentativa_em = excluded.ultima_tentativa_em
    where entregas_sinal.desfecho is null
  returning * into v_linha;
  if v_linha.sinal_id is null then       -- já terminal: nada a contar
    return jsonb_build_object('registrado', false, 'motivo', 'desfecho_ja_selado');
  end if;
  return jsonb_build_object('registrado', true, 'tentativas', v_linha.tentativas);
end $$;

create or replace function fn_registrar_entrega_cartao(
  p_sinal_id uuid, p_agora timestamptz default now()
) returns jsonb
language plpgsql
set search_path = public, pg_temp
as $$
declare v_linha entregas_sinal;
begin
  insert into entregas_sinal (sinal_id, desfecho, entregue_em)
  values (p_sinal_id, 'entregue', p_agora)
  on conflict (sinal_id) do update
    set desfecho = 'entregue', entregue_em = p_agora
    where entregas_sinal.desfecho is null
  returning * into v_linha;
  if v_linha.sinal_id is null then
    return jsonb_build_object('registrado', false, 'motivo', 'desfecho_ja_selado');
  end if;
  return jsonb_build_object('registrado', true);
end $$;

-- Sela o que não tem mais volta: passou o apito sem entrega. O motivo da ÚLTIMA
-- tentativa é o que distingue perda de MERCADO (preço fugiu) de perda OPERACIONAL
-- (nada chegou a ser tentado) — a distinção que o P0.7 mede.
create or replace function fn_selar_entregas(p_agora timestamptz default now())
returns int
language plpgsql
set search_path = public, pg_temp
as $$
declare v_n int;
begin
  with selados as (
    insert into entregas_sinal (sinal_id, desfecho, ultimo_motivo, ultima_tentativa_em)
    select s.id,
           case
             when x.ultimo_motivo like 'preco%'   then 'suprimido_preco'
             when x.ultimo_motivo like 'frescor%' then 'suprimido_frescor'
             else 'nao_entregue_timeout'
           end,
           x.ultimo_motivo, p_agora
    from sinais s
    join eventos ev on ev.id = s.evento_id
    left join entregas_sinal x on x.sinal_id = s.id
    where s.status = 'confirmado' and ev.inicio_utc <= p_agora
      and (x.sinal_id is null or x.desfecho is null)
    on conflict (sinal_id) do update
      set desfecho = excluded.desfecho, ultima_tentativa_em = excluded.ultima_tentativa_em
      where entregas_sinal.desfecho is null
    returning 1
  )
  select count(*) into v_n from selados;
  return v_n;
end $$;

-- ================= P1.4 — episódio de kill switch =================

create table episodios_kill_switch (
  id            bigint generated always as identity primary key,
  aberto_em     timestamptz not null default now(),
  pico          numeric,
  drawdown_pct  numeric,
  encerrado_em  timestamptz,
  motivo_fim    text
);
-- No máximo UM episódio aberto: é o que torna "um alerta por suspensão" verificável
-- pelo banco, e não pela sorte de a notificação anterior ainda estar pendente.
create unique index ux_episodio_kill_switch_aberto
  on episodios_kill_switch ((true)) where encerrado_em is null;

create trigger tg_episodios_ks_del before delete on episodios_kill_switch
  for each row execute function fn_bloqueia_delete();
alter table episodios_kill_switch enable row level security;

create or replace function fn_abrir_episodio_kill_switch(
  p_pico numeric default null, p_drawdown_pct numeric default null,
  p_agora timestamptz default now()
) returns jsonb
language plpgsql
set search_path = public, pg_temp
as $$
declare v_id bigint;
begin
  insert into episodios_kill_switch (aberto_em, pico, drawdown_pct)
  values (p_agora, p_pico, p_drawdown_pct)
  returning id into v_id;
  return jsonb_build_object('abriu', true, 'episodio_id', v_id);
exception when unique_violation then
  -- Já há episódio aberto: a suspensão é a MESMA. Alertar de novo é spam.
  select id into v_id from episodios_kill_switch where encerrado_em is null;
  return jsonb_build_object('abriu', false, 'episodio_id', v_id,
                            'motivo', 'episodio_ja_aberto');
end $$;

create or replace function fn_encerrar_episodio_kill_switch(
  p_motivo text default null, p_agora timestamptz default now()
) returns jsonb
language plpgsql
set search_path = public, pg_temp
as $$
declare v_id bigint;
begin
  update episodios_kill_switch
     set encerrado_em = p_agora, motivo_fim = p_motivo
   where encerrado_em is null
  returning id into v_id;
  return jsonb_build_object('encerrou', v_id is not null, 'episodio_id', v_id);
end $$;

-- ================= P1.6 — backoff e desistência da outbox =================

alter table notificacoes add column proxima_tentativa_em timestamptz;
alter table notificacoes add column ultimo_erro text;

alter table notificacoes drop constraint notificacoes_status_check;
alter table notificacoes add constraint notificacoes_status_check
  check (status in ('pendente','enviando','entregue','interno','morta'));

comment on column notificacoes.proxima_tentativa_em is
  'Backoff: a linha só volta à fila a partir daqui. Sem isto, uma falha permanente '
  '(token revogado) era retentada a cada ciclo, para sempre.';

-- Reivindicação passa a respeitar o backoff.
create or replace function fn_reivindicar_notificacoes(
  p_agora timestamptz, p_limite int default 200, p_reclaim_s numeric default 300
) returns setof notificacoes
language plpgsql
set search_path = public, pg_temp
as $$
begin
  return query
  update notificacoes n
     set status = 'enviando',
         tentativas = n.tentativas + 1,
         ultima_tentativa_em = p_agora
   where n.id in (
     select c.id from notificacoes c
      where (c.status = 'pendente'
             and (c.proxima_tentativa_em is null or c.proxima_tentativa_em <= p_agora))
         or (c.status = 'enviando'
             and c.ultima_tentativa_em < p_agora - (p_reclaim_s || ' seconds')::interval)
      order by c.id
      limit p_limite
      for update skip locked
   )
  returning n.*;
end $$;

-- Devolve com backoff exponencial; no teto, DESISTE (morta) e sela o desfecho de
-- entrega do sinal como perda operacional.
create or replace function fn_devolver_notificacao(
  p_id bigint, p_erro text default null, p_agora timestamptz default now(),
  p_max_tentativas int default 6, p_base_s numeric default 30
) returns jsonb
language plpgsql
set search_path = public, pg_temp
as $$
declare v_n notificacoes; v_espera numeric;
begin
  select * into v_n from notificacoes where id = p_id and status = 'enviando' for update;
  if not found then
    return jsonb_build_object('devolvido', false, 'motivo', 'nao_estava_enviando');
  end if;

  if v_n.tentativas >= p_max_tentativas then
    update notificacoes set status = 'morta', ultimo_erro = p_erro where id = p_id;
    if v_n.tipo = 'sinal' and v_n.sinal_id is not null then
      insert into entregas_sinal (sinal_id, desfecho, ultimo_motivo, ultima_tentativa_em)
      values (v_n.sinal_id, 'nao_entregue_falha',
              'outbox desistiu após ' || v_n.tentativas || ' tentativas', p_agora)
      on conflict (sinal_id) do update
        set desfecho = 'nao_entregue_falha', ultimo_motivo = excluded.ultimo_motivo
        where entregas_sinal.desfecho is null;
    end if;
    return jsonb_build_object('devolvido', false, 'morta', true,
                              'tentativas', v_n.tentativas);
  end if;

  -- 30 s, 60, 120, 240, 480, 960 — teto de 1 h.
  v_espera := least(p_base_s * (2 ^ greatest(v_n.tentativas - 1, 0))::numeric, 3600::numeric);
  update notificacoes
     set status = 'pendente', ultimo_erro = p_erro,
         proxima_tentativa_em = p_agora + (v_espera || ' seconds')::interval
   where id = p_id;
  return jsonb_build_object('devolvido', true, 'espera_s', v_espera,
                            'tentativas', v_n.tentativas);
end $$;

-- ================= Segurança (mesmo desenho da 0002) =================

revoke all on function fn_registrar_tentativa_cartao(uuid, text, timestamptz)      from public, anon, authenticated;
revoke all on function fn_registrar_entrega_cartao(uuid, timestamptz)              from public, anon, authenticated;
revoke all on function fn_selar_entregas(timestamptz)                              from public, anon, authenticated;
revoke all on function fn_abrir_episodio_kill_switch(numeric, numeric, timestamptz) from public, anon, authenticated;
revoke all on function fn_encerrar_episodio_kill_switch(text, timestamptz)         from public, anon, authenticated;
revoke all on function fn_devolver_notificacao(bigint, text, timestamptz, int, numeric) from public, anon, authenticated;
grant execute on function fn_registrar_tentativa_cartao(uuid, text, timestamptz)      to service_role;
grant execute on function fn_registrar_entrega_cartao(uuid, timestamptz)              to service_role;
grant execute on function fn_selar_entregas(timestamptz)                              to service_role;
grant execute on function fn_abrir_episodio_kill_switch(numeric, numeric, timestamptz) to service_role;
grant execute on function fn_encerrar_episodio_kill_switch(text, timestamptz)         to service_role;
grant execute on function fn_devolver_notificacao(bigint, text, timestamptz, int, numeric) to service_role;
