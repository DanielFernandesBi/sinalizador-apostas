-- 0008 — O L2 não analisa sinal de partida já iniciada (Sugestão nº 10, achado #4).
--
-- `sinais_aguardando_crivo` devolvia todo sinal em 'aguardando_crivo', sem olhar o
-- início da partida. Com a partida em curso, um CONFIRMA do crivo produziria um
-- cartão para uma aposta que não existe mais — e o item disputaria com o
-- `fn_timeout_crivo` do L4 (que o converte em `timeout_crivo`), gerando corrida entre
-- confirmar e expirar o mesmo sinal.
--
-- A fila passa a excluir, no BANCO, os sinais cujo evento já começou. Quem os
-- resolve é o `fn_timeout_crivo` (L4), com desfecho terminal próprio.

create or replace function fn_fila_crivo(p_agora timestamptz, p_limite int default 50)
returns setof sinais
language sql
stable
set search_path = public, pg_temp
as $$
  select s.*
  from sinais s
  join eventos e on e.id = s.evento_id
  where s.status = 'aguardando_crivo'
    and e.inicio_utc > p_agora        -- partida ainda não começou
  order by s.criado_em
  limit p_limite
$$;

revoke all on function fn_fila_crivo(timestamptz, int) from public, anon, authenticated;
grant execute on function fn_fila_crivo(timestamptz, int) to service_role;
