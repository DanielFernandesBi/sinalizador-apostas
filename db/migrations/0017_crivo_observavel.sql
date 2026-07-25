-- 0017 — O que a chamada ao modelo realmente fez (P1.7).
--
-- PROBLEMA: `crivos` guardava tokens de entrada/saída e custo, e nada mais. Faltava
-- justamente o que distingue uma avaliação sadia de uma que quase não aconteceu:
--
--   • `stop_reason` — nunca foi lido pelo código nem gravado. `pause_turn` (o laço
--     server-side da busca web bateu o limite de iterações), `max_tokens` (resposta
--     cortada) e `refusal` chegavam ao chamador como TEXTO, falhavam na validação de
--     saída e viravam `erro` PERMANENTE. Como o candidato só tem um registro na vida
--     do evento (Sugestão nº 11), um soluço da API apagava a aposta da amostra para
--     sempre — e a auditoria não tinha como saber que foi isso.
--   • `buscas_web` / `continuacoes` — o caminho profundo podia gastar N chamadas sem
--     deixar rastro de quantas.
--   • tokens de CACHE — `input_tokens` é só o resto NÃO cacheado. Somar apenas ele
--     subestima o custo sempre que o Manual (system prompt) é servido do cache, que é
--     o caso comum. Leitura custa ~0,1× e escrita ~1,25×.
--
-- `fn_concluir_crivo` monta o INSERT a partir de chaves nomeadas do jsonb, então
-- chave nova sem coluna nova seria DESCARTADA EM SILÊNCIO — pior que erro, porque o
-- código pareceria estar registrando.
--
-- Todas as colunas são opcionais: crivos já gravados continuam válidos com NULL.

alter table crivos add column stop_reason           text;
alter table crivos add column buscas_web            int;
alter table crivos add column continuacoes          int;
alter table crivos add column tokens_cache_leitura  int;
alter table crivos add column tokens_cache_escrita  int;

comment on column crivos.stop_reason is
  'Por que o modelo parou (end_turn | max_tokens | pause_turn | refusal | ...). '
  'Só `end_turn` produz parecer; os demais não geram crivo — ficam no log.';
comment on column crivos.buscas_web is
  'Buscas web do caminho profundo. A cobrança por USO da ferramenta é separada dos '
  'tokens e NÃO entra em custo_usd (ver PC-CUSTO-FERRAMENTA).';

-- Cópia FIEL da 0013 com um único acréscimo: as colunas novas no INSERT.
-- Reescrevê-la de memória teria perdido o tratamento de `unique_violation`
-- (`crivo_ja_existe`), o `raise` em sinal inexistente e o escopo do lock.
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
                      tokens_entrada, tokens_saida, custo_usd,
                      stop_reason, buscas_web, continuacoes,
                      tokens_cache_leitura, tokens_cache_escrita)
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
    (p_crivo->>'custo_usd')::numeric,
    p_crivo->>'stop_reason',
    (p_crivo->>'buscas_web')::int,
    (p_crivo->>'continuacoes')::int,
    (p_crivo->>'tokens_cache_leitura')::int,
    (p_crivo->>'tokens_cache_escrita')::int
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
