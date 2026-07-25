-- 0012 — Unidade estatística por aposta lógica + trava de kickoff (P0.1, P0.5, P0.6).
--
-- P0.5 — VETO NÃO IMPEDIA REEMISSÃO. `ux_sinais_candidato_aberto` era PARCIAL
-- (`status in ('aguardando_crivo','confirmado')`). Assim que o sinal virava vetado,
-- erro, expirado ou timeout_crivo, a chave ficava livre e o L1 criava OUTRO sinal
-- para a mesma partida+mercado+linha+seleção+casa no ciclo seguinte. Consequência:
-- o mesmo candidato era submetido ao L2 várias vezes até, por variação estocástica
-- do modelo, ser confirmado — e o mesmo desfecho esportivo entrava na amostra como
-- se fossem apostas independentes.
--
-- P0.6 — CANDIDATO SOMBRA INFLAVA A AMOSTRA. Abortos eram deduplicados só dentro do
-- lookback do L1 (1 h). Passada a hora, a mesma aposta lógica gerava outra linha, e
-- cada linha recebia seu CLV. Chegar a "200 amostras" com muito menos de 200 apostas
-- independentes esvazia o gate `amostra_minima` — que é PÉTREO justamente porque é
-- ele que autoriza conclusões.
--
-- REGRA: uma chave de candidato produz NO MÁXIMO UM registro durante toda a vida do
-- evento, qualquer que seja o desfecho. Reentrada exigiria um conceito formal de
-- "nova oportunidade" (revisão de origem, preço materialmente novo, cooldown e regra
-- que impeça contar o mesmo desfecho esportivo como amostras independentes) — isso
-- não existe no MVP e não se improvisa.
--
-- P0.1 — NADA NASCE DEPOIS DO APITO. O L1 lê a janela de lookback e uma revisão
-- pré-jogo segue "fresca" pelo gate de idade por até 600 s: sem trava, minutos após
-- o início ainda nasciam sinal, aborto e candidato_sombra. Pior: o L4 pode já ter
-- finalizado o evento, e item criado depois disso NÃO reabre
-- `clv_eventos_finalizados` — ficaria sem CLV para sempre, e o evento com um item
-- eternamente pendente. A trava de aplicação (Python) não basta sob concorrência:
-- entre o `if` e o `INSERT` o relógio anda. Aqui ela é feita com a linha do evento
-- TRAVADA, na mesma transação do INSERT.
--
-- Seguro: `sinais` e `abortos_l1` estão VAZIAS (0 linhas).

-- ---------- 1. Unicidade por aposta lógica ----------

drop index ux_sinais_candidato_aberto;

create unique index ux_sinais_candidato
  on sinais (chave_candidato) where chave_candidato is not null;

create unique index ux_abortos_candidato_unico
  on abortos_l1 (chave_candidato) where chave_candidato is not null;

-- ---------- 2. Criação guardada de sinal ----------

create or replace function fn_registrar_sinal(p_dados jsonb) returns jsonb
language plpgsql
set search_path = public, pg_temp
as $$
declare
  v_inicio timestamptz;
  v_evento uuid := (p_dados->>'evento_id')::uuid;
  v_id uuid;
begin
  -- `for update` trava a linha do evento: a verificação e o INSERT ficam na mesma
  -- transação, então o kickoff não pode "acontecer" entre uma coisa e outra.
  select e.inicio_utc into v_inicio from eventos e where e.id = v_evento for update;
  if not found then
    raise exception 'evento % inexistente', v_evento;
  end if;
  if v_inicio is null or v_inicio <= now() then
    raise exception 'partida já iniciada (inicio=%) — nada é criado após o apito', v_inicio;
  end if;
  if exists (select 1 from clv_eventos_finalizados f where f.evento_id = v_evento) then
    raise exception 'evento % já finalizado no L4 — item novo nunca receberia CLV', v_evento;
  end if;

  insert into sinais (
    id, evento_id, casa_venue_id, gatilho, gatilho_anomalo, caminho, mercado, selecao,
    linha, p_justa, odd_referencia, odd_venue, edge_liquido_pct, stake_pct,
    odd_minima_aceitavel, dossie, chave_candidato
  ) values (
    coalesce((p_dados->>'id')::uuid, gen_random_uuid()),
    v_evento,
    (p_dados->>'casa_venue_id')::uuid,
    p_dados->>'gatilho',
    coalesce((p_dados->>'gatilho_anomalo')::boolean, false),
    p_dados->>'caminho',
    p_dados->>'mercado',
    p_dados->>'selecao',
    (p_dados->>'linha')::numeric,
    (p_dados->>'p_justa')::numeric,
    (p_dados->>'odd_referencia')::numeric,
    (p_dados->>'odd_venue')::numeric,
    (p_dados->>'edge_liquido_pct')::numeric,
    (p_dados->>'stake_pct')::numeric,
    (p_dados->>'odd_minima_aceitavel')::numeric,
    p_dados->'dossie',
    p_dados->>'chave_candidato'
  ) returning id into v_id;
  -- `status` e `criado_em` ficam nos defaults do schema (aguardando_crivo / now()).

  return jsonb_build_object('id', v_id, 'criado', true);
exception when unique_violation then
  -- Já existe registro para este candidato (P0.5): é o resultado desejado.
  return jsonb_build_object('id', null, 'criado', false, 'motivo', 'candidato_ja_registrado');
end $$;

-- ---------- 3. Criação guardada de aborto / candidato_sombra ----------

create or replace function fn_registrar_aborto(p_dados jsonb) returns jsonb
language plpgsql
set search_path = public, pg_temp
as $$
declare
  v_inicio timestamptz;
  v_evento uuid := (p_dados->>'evento_id')::uuid;
  v_id bigint;
begin
  if v_evento is not null then
    select e.inicio_utc into v_inicio from eventos e where e.id = v_evento for update;
    if found then
      if v_inicio is null or v_inicio <= now() then
        raise exception 'partida já iniciada (inicio=%) — nada é criado após o apito', v_inicio;
      end if;
      if exists (select 1 from clv_eventos_finalizados f where f.evento_id = v_evento) then
        raise exception 'evento % já finalizado no L4 — item novo nunca receberia CLV', v_evento;
      end if;
    end if;
  end if;

  insert into abortos_l1 (gatilho, evento_id, gate_reprovado, dossie_parcial,
                          clv_rastrear, chave_candidato)
  values (
    p_dados->>'gatilho',
    v_evento,
    p_dados->>'gate_reprovado',
    coalesce(p_dados->'dossie_parcial', '{}'::jsonb),
    coalesce((p_dados->>'clv_rastrear')::boolean, false),
    p_dados->>'chave_candidato'
  ) returning id into v_id;

  return jsonb_build_object('id', v_id, 'criado', true);
exception when unique_violation then
  return jsonb_build_object('id', null, 'criado', false, 'motivo', 'candidato_ja_registrado');
end $$;

-- ---------- 4. Superfície de execução (postura da 0002) ----------

revoke all on function fn_registrar_sinal(jsonb) from public, anon, authenticated;
revoke all on function fn_registrar_aborto(jsonb) from public, anon, authenticated;
grant execute on function fn_registrar_sinal(jsonb) to service_role;
grant execute on function fn_registrar_aborto(jsonb) to service_role;
