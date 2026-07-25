-- 0013 — Conclusão atômica do crivo (P0.2 da 3ª auditoria).
--
-- PROBLEMA: a fila do L2 filtra corretamente partidas ainda não iniciadas, mas essa
-- verificação acontece ANTES da chamada ao modelo. Uma chamada profunda pode começar
-- às 20h59:50 e terminar às 21h00:20. Depois da resposta, o código fazia DUAS
-- operações soltas: `insert into crivos` e depois a transição de status. Entre elas
-- (e durante a chamada), o L4 pode rodar `fn_timeout_crivo`.
--
-- Estados inconsistentes que isso produzia — todos alcançáveis:
--   • crivo gravado e sinal em `timeout_crivo` (a transição falhou, o crivo ficou);
--   • crivo gravado e sinal ainda em `aguardando_crivo`;
--   • status alterado DEPOIS do apito (sinal confirmado para partida em curso);
--   • retentativa do L2 falhando pela unicidade de `crivos.sinal_id`;
--   • cartão emitido para uma aposta que já não existe.
--
-- SOLUÇÃO: uma transação só. A função trava o sinal, relê o evento, confere que o
-- status ainda é `aguardando_crivo` e que o kickoff não passou, e só então grava o
-- crivo e transiciona. Se a partida começou durante a chamada ao modelo, o sinal vai
-- para `timeout_crivo` SEM gravar crivo — o veredicto perdeu a validade, e registrar
-- um parecer sobre aposta inexistente sujaria a auditoria do crivo (`vw_clv_por_veto`).
--
-- A função NÃO levanta em conflito esperado: devolve `aplicado=false` com o motivo,
-- para o L2 contabilizar sem derrubar o ciclo.

create or replace function fn_concluir_crivo(
  p_sinal_id uuid,
  p_crivo    jsonb,
  p_status   text
) returns jsonb
language plpgsql
set search_path = public, pg_temp
as $$
declare
  v_status_atual text;
  v_evento uuid;
  v_inicio timestamptz;
begin
  if p_status not in ('confirmado', 'vetado') then
    raise exception 'status de conclusão inválido: % (esperado confirmado|vetado)', p_status;
  end if;

  -- Trava o sinal: a leitura do estado e a escrita ficam na mesma transação.
  select s.status, s.evento_id into v_status_atual, v_evento
    from sinais s where s.id = p_sinal_id for update;
  if not found then
    raise exception 'sinal % inexistente', p_sinal_id;
  end if;
  if v_status_atual <> 'aguardando_crivo' then
    -- O L4 (timeout) ou outra instância do L2 chegou antes. Não é erro.
    return jsonb_build_object('aplicado', false, 'motivo', 'status_ja_mudou',
                              'status', v_status_atual);
  end if;

  select e.inicio_utc into v_inicio from eventos e where e.id = v_evento;
  if v_inicio is null or v_inicio <= now() then
    -- A partida começou durante a chamada ao modelo: o veredicto perdeu validade.
    -- Fecha como timeout_crivo, SEM gravar parecer.
    update sinais set status = 'timeout_crivo' where id = p_sinal_id;
    return jsonb_build_object('aplicado', false, 'motivo', 'kickoff_ultrapassado',
                              'status', 'timeout_crivo');
  end if;

  insert into crivos (sinal_id, verdict, caminho_executado, fatores, motivo_veto,
                      fontes_consultadas, observacao, modelo, latencia_ms,
                      tokens_entrada, tokens_saida, custo_usd)
  values (
    p_sinal_id,
    p_crivo->>'verdict',
    p_crivo->>'caminho_executado',
    coalesce(p_crivo->'fatores', '[]'::jsonb),
    p_crivo->'motivo_veto',
    coalesce(p_crivo->'fontes_consultadas', '[]'::jsonb),
    p_crivo->>'observacao',
    p_crivo->>'modelo',
    (p_crivo->>'latencia_ms')::int,
    (p_crivo->>'tokens_entrada')::int,
    (p_crivo->>'tokens_saida')::int,
    (p_crivo->>'custo_usd')::numeric
  );

  update sinais set status = p_status where id = p_sinal_id;

  return jsonb_build_object('aplicado', true, 'status', p_status);
exception when unique_violation then
  -- `crivos.sinal_id` é único: já há parecer para este sinal (retentativa após
  -- falha parcial do código antigo). Nada a fazer — não é erro.
  return jsonb_build_object('aplicado', false, 'motivo', 'crivo_ja_existe',
                            'status', v_status_atual);
end $$;

revoke all on function fn_concluir_crivo(uuid, jsonb, text) from public, anon, authenticated;
grant execute on function fn_concluir_crivo(uuid, jsonb, text) to service_role;
