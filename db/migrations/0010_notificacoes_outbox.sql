-- 0010 — Outbox de notificações: registrar ANTES de enviar (achado #7 do 2º ciclo, L3).
--
-- PROBLEMA: `emitir_confirmados` chamava o Telegram e SÓ DEPOIS inseria a linha em
-- `notificacoes`. Se o Telegram aceitasse e o INSERT falhasse (rede/banco), o próximo
-- ciclo não encontraria registro do cartão e ENVIARIA DE NOVO. Além disso, dois
-- processos do L3 rodando juntos enviariam o mesmo cartão duas vezes: nada impedia.
--
-- SOLUÇÃO (outbox): a linha nasce `pendente` ANTES de qualquer envio, protegida por
-- chave idempotente (um cartão por sinal). O envio é uma máquina de estados:
--
--     pendente → enviando → entregue
--
-- `enviando` é a reserva: o remetente reivindica a linha ATOMICAMENTE (`for update
-- skip locked`), então dois processos nunca disputam a mesma. Se um deles morrer no
-- meio, a linha fica `enviando` e é RECUPERADA após uma janela, voltando à fila.
--
-- JANELA RESIDUAL, ASSUMIDA: a API do Telegram não oferece chave de idempotência em
-- `sendMessage`. Entre "o Telegram aceitou" e "o banco marcou entregue" existe um
-- instante em que uma queda produz reenvio. Isso é irredutível — e a escolha é
-- deliberada: **é preferível uma duplicata rara a perder um sinal em silêncio.**
--
-- `entregue` (booleano) some: ele confundia "já enviado" com "registro interno que
-- nunca vai ao bot" — dois significados numa flag só. Vira `status`, com `interno`
-- explícito. `notificacoes` está VAZIA (0 linhas), então nada é reinterpretado.

alter table notificacoes drop column entregue;
alter table notificacoes rename column enviado_em to criado_em;   -- era o instante da CRIAÇÃO

alter table notificacoes add column status text not null default 'pendente'
  check (status in (
    'pendente',    -- a enviar
    'enviando',    -- reservada por um remetente (reserva expira e volta à fila)
    'entregue',    -- o Telegram confirmou
    'interno'      -- registro de auditoria: nunca vai ao bot
  ));
alter table notificacoes add column tentativas int not null default 0;
alter table notificacoes add column ultima_tentativa_em timestamptz;
alter table notificacoes add column entregue_em timestamptz;

-- Chave idempotente: UM cartão por sinal. É o que torna impossível o cartão duplicado
-- mesmo sob corrida — a garantia deixa de ser "consultei antes e não achei".
-- Restrita a tipo='sinal': um mesmo sinal PODE ter vários registros administrativos
-- (erro do crivo, supressão no envio), e travá-los seria perder registro de auditoria.
create unique index ux_notificacao_cartao
  on notificacoes (sinal_id) where tipo = 'sinal' and sinal_id is not null;

create index ix_notificacoes_fila on notificacoes (status, id) where status in ('pendente','enviando');

-- ---------- Reivindicação atômica ----------

-- Move para `enviando` e devolve as linhas reivindicadas. `skip locked` faz dois
-- processos concorrentes pegarem conjuntos DISJUNTOS em vez de disputarem a mesma.
-- `p_reclaim_s` recupera linhas presas em `enviando` por um processo que morreu.
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
      where c.status = 'pendente'
         or (c.status = 'enviando'
             and c.ultima_tentativa_em < p_agora - (p_reclaim_s || ' seconds')::interval)
      order by c.id
      limit p_limite
      for update skip locked
   )
  returning n.*;
end $$;

create or replace function fn_marcar_notificacao_entregue(p_id bigint, p_agora timestamptz)
returns boolean
language plpgsql
set search_path = public, pg_temp
as $$
declare v_n int;
begin
  update notificacoes
     set status = 'entregue', entregue_em = p_agora
   where id = p_id and status <> 'entregue';
  get diagnostics v_n = row_count;
  return v_n > 0;
end $$;

-- Falha de envio devolve a linha à fila imediatamente (sem esperar a janela de
-- recuperação): o retry é do próximo ciclo, não de daqui a 5 minutos.
create or replace function fn_devolver_notificacao(p_id bigint)
returns boolean
language plpgsql
set search_path = public, pg_temp
as $$
declare v_n int;
begin
  update notificacoes set status = 'pendente'
   where id = p_id and status = 'enviando';
  get diagnostics v_n = row_count;
  return v_n > 0;
end $$;

revoke all on function fn_reivindicar_notificacoes(timestamptz, int, numeric) from public, anon, authenticated;
revoke all on function fn_marcar_notificacao_entregue(bigint, timestamptz) from public, anon, authenticated;
revoke all on function fn_devolver_notificacao(bigint) from public, anon, authenticated;
grant execute on function fn_reivindicar_notificacoes(timestamptz, int, numeric) to service_role;
grant execute on function fn_marcar_notificacao_entregue(bigint, timestamptz) to service_role;
grant execute on function fn_devolver_notificacao(bigint) to service_role;
