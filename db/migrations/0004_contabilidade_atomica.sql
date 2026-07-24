-- 0004 — Contrato contábil, atomicidade e idempotência da banca (achado #1 do 2º ciclo).
--
-- PROBLEMA (verdade do dinheiro):
--   O registro debitava o STAKE ('aposta', -stake) e a liquidação creditava o valor
--   digitado à mão em `--retorno`, documentado como "retorno LÍQUIDO" (lucro). Uma green
--   de R$20 @ 2,05 fazia a banca variar -20 + 21 = +R$1, quando o correto é +R$21. O
--   sistema misturava dois contratos (débito bruto + crédito líquido) e cobrava a perda
--   do stake duas vezes. Banca errada contamina o sizing de Kelly E o kill switch (P9).
--
-- CONTRATO FORMALIZADO (bruto):
--   registro    → debita o STAKE.
--   liquidação  → credita o PAYOUT BRUTO, DERIVADO de (resultado, stake, odd). Nunca
--                 digitado. O lucro líquido é gravado como informação derivada.
--
--   resultado    payout bruto creditado        variação final da banca
--   green        stake × odd                   +stake × (odd − 1)
--   red          0                             −stake
--   void         stake                          0
--   meio_green   (stake/2 × odd) + stake/2     +stake × (odd − 1) / 2
--   meio_red     stake/2                       −stake/2
--
-- ATOMICIDADE: registro e liquidação passam a ser UMA transação cada (função SQL). Antes
-- eram duas chamadas HTTP separadas (INSERT aposta → INSERT ledger; UPDATE aposta →
-- INSERT ledger): falha no meio deixava a banca inconsistente — e a imutabilidade
-- (P7) impede reparar com DELETE/UPDATE. Agora ou tudo é gravado, ou nada é.
--
-- IDEMPOTÊNCIA: índice único parcial garante no máximo UM débito e UM crédito por
-- aposta, mesmo sob corrida entre processos.
--
-- O saldo passa a ser lido DENTRO da transação (com lock), não pelo app antes de
-- chamar (TOCTOU: dois processos liam o mesmo `saldo_antes` e gravavam `saldo_apos`
-- inconsistentes em `vw_banca`, que deriva o saldo do último lançamento).
--
-- Seguro aplicar: `apostas` e `banca_ledger` estão VAZIAS (0 linhas) — nenhum dado
-- histórico é reinterpretado por esta migration.

-- ---------- 1. Colunas: o nome passa a dizer a verdade ----------

-- `retorno_liquido` guardaria o payout BRUTO sob o novo contrato — uma armadilha
-- permanente para relatório e auditoria. Vira `lucro_liquido` (o que o nome sempre
-- prometeu) e ganha o par `payout_bruto` (o que de fato entra na banca). Guardar os
-- dois deixa a aposta autoexplicativa e permite conferir o ledger sem recalcular.
alter table apostas rename column retorno_liquido to lucro_liquido;
alter table apostas add column payout_bruto numeric;

-- ---------- 2. Trigger de liquidação única: whitelist atualizada ----------

-- O `fn_apostas_update` faz whitelist LITERAL das colunas que podem mudar na
-- liquidação. Sem incluir as novas, toda liquidação seria rejeitada pelo trigger.
create or replace function fn_apostas_update() returns trigger
language plpgsql set search_path = public, pg_temp as $$
begin
  if old.resultado <> 'pendente' or new.resultado = 'pendente'
     or (to_jsonb(new) - array['resultado','payout_bruto','lucro_liquido','liquidada_em'])
        <> (to_jsonb(old) - array['resultado','payout_bruto','lucro_liquido','liquidada_em']) then
    raise exception 'apostas: apenas liquidação única (pendente → resultado final)';
  end if;
  return new;
end $$;

-- ---------- 3. Idempotência do ledger ----------

-- No máximo UM lançamento de cada tipo por aposta: uma aposta não pode receber dois
-- débitos nem duas liquidações, nem sob corrida entre processos. É o backstop de
-- banco para a trava de aplicação (`resultado = 'pendente'`).
create unique index ux_ledger_aposta_tipo
  on banca_ledger (aposta_id, tipo)
  where aposta_id is not null;

-- ---------- 4. Registro da aposta: uma transação ----------

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

  -- Serializa TODA escrita no ledger: `vw_banca` deriva o saldo do último
  -- `saldo_apos`, então dois processos concorrentes não podem lê-lo ao mesmo tempo.
  perform pg_advisory_xact_lock(hashtext('banca_ledger'));

  v_stake := round(p_stake, 2);
  select coalesce((select saldo_apos from banca_ledger order by id desc limit 1), 0)
    into v_saldo;

  insert into apostas (sinal_id, casa_id, odd_executada, stake_valor)
  values (p_sinal_id, p_casa_id, p_odd, v_stake)
  returning * into v_aposta;

  -- Se este INSERT falhar (índice de idempotência, FK, etc.), a aposta acima é
  -- desfeita junto: a transação é uma só.
  insert into banca_ledger (tipo, valor, aposta_id, motivo, saldo_apos)
  values ('aposta', -v_stake, v_aposta.id,
          'execução do sinal ' || p_sinal_id::text, v_saldo - v_stake);

  return jsonb_build_object(
    'aposta', to_jsonb(v_aposta),
    'saldo',  v_saldo - v_stake
  );
end $$;

-- ---------- 5. Liquidação: uma transação, payout derivado ----------

create or replace function fn_liquidar_aposta(
  p_aposta_id uuid,
  p_resultado text
) returns jsonb
language plpgsql
set search_path = public, pg_temp
as $$
declare
  v_ap     apostas;
  v_payout numeric;
  v_lucro  numeric;
  v_saldo  numeric;
begin
  if p_resultado not in ('green','red','void','meio_green','meio_red') then
    raise exception 'resultado inválido: % (esperado green|red|void|meio_green|meio_red)',
                    p_resultado;
  end if;

  perform pg_advisory_xact_lock(hashtext('banca_ledger'));

  -- `for update` trava a linha: uma segunda liquidação concorrente espera aqui e,
  -- ao prosseguir, encontra `resultado <> 'pendente'` e é recusada.
  select * into v_ap from apostas where id = p_aposta_id for update;
  if not found then
    raise exception 'aposta % não encontrada', p_aposta_id;
  end if;
  if v_ap.resultado <> 'pendente' then
    raise exception 'aposta % já liquidada (resultado=%) — a liquidação é única',
                    p_aposta_id, v_ap.resultado;
  end if;

  -- Payout BRUTO derivado — nunca digitado (a fonte do erro que esta migration corrige).
  v_payout := round(case p_resultado
    when 'green'      then v_ap.stake_valor * v_ap.odd_executada
    when 'red'        then 0
    when 'void'       then v_ap.stake_valor
    when 'meio_green' then (v_ap.stake_valor / 2 * v_ap.odd_executada) + v_ap.stake_valor / 2
    when 'meio_red'   then v_ap.stake_valor / 2
  end, 2);
  v_lucro := round(v_payout - v_ap.stake_valor, 2);

  select coalesce((select saldo_apos from banca_ledger order by id desc limit 1), 0)
    into v_saldo;

  update apostas
     set resultado     = p_resultado,
         payout_bruto  = v_payout,
         lucro_liquido = v_lucro,
         liquidada_em  = now()
   where id = p_aposta_id;

  -- 'red' credita 0: o lançamento existe assim mesmo, para que o índice de
  -- idempotência registre que ESTA aposta já foi liquidada.
  insert into banca_ledger (tipo, valor, aposta_id, motivo, saldo_apos)
  values ('liquidacao', v_payout, p_aposta_id,
          'liquidação ' || p_resultado, v_saldo + v_payout);

  return jsonb_build_object(
    'aposta_id',     p_aposta_id,
    'resultado',     p_resultado,
    'payout_bruto',  v_payout,
    'lucro_liquido', v_lucro,
    'saldo',         v_saldo + v_payout
  );
end $$;

-- ---------- 6. Superfície de execução (mesma postura da 0002) ----------

-- Desenho service-role-only: nem anon nem authenticated executam funções que movem
-- a banca. São SECURITY INVOKER (padrão) — sem escalada de privilégio; o RLS do
-- chamador continua valendo, e só o service_role (bypass RLS) escreve.
revoke all on function fn_registrar_aposta(uuid, uuid, numeric, numeric) from public, anon, authenticated;
revoke all on function fn_liquidar_aposta(uuid, text) from public, anon, authenticated;
grant execute on function fn_registrar_aposta(uuid, uuid, numeric, numeric) to service_role;
grant execute on function fn_liquidar_aposta(uuid, text) to service_role;
