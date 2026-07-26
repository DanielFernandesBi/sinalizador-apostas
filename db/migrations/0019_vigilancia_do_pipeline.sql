-- 0019 — O vigia passa a vigiar o pipeline inteiro, e o silêncio vira episódio.
-- (auditoria P1: item P1.5)
--
-- P1.5 — O VIGIA OLHAVA DOIS DOS SETE DAEMONS. `DAEMONS_ESPERADOS_PADRAO` era
--   ("l0_referencia", "l0_varejo"). O L1, o L2, o L3 e o L4 PULSAM — `banco.pulsar`
--   está em todos eles — e ninguém lia esses pulsos. O L2 podia estar morto há um dia:
--   os candidatos ficavam empilhados em `aguardando_crivo` e nada acontecia. Esse é o
--   modo de falha que este sistema mais teme: não é emitir errado, é DEIXAR DE EMITIR
--   em silêncio, que é indistinguível de "não houve oportunidade". No L4 é pior ainda,
--   porque sem L4 não há CLV — e CLV é a única evidência que homologa mercado (E6.4) e
--   libera dinheiro real (E7). O sistema pararia de acumular a prova de que funciona
--   sem que nada no sistema dissesse isso.
--
--   UM LIMIAR SÓ TAMBÉM ESTAVA ERRADO. 7200 s aplicado ao L2 (cadência 30 s) são 240
--   ciclos perdidos antes de qualquer alerta; aplicado ao L0 (cadência 3600 s) são
--   dois. O mesmo número significa "catástrofe" para um daemon e "normal" para outro.
--   O limiar tem que sair da CADÊNCIA de cada um — por isso o roster do vigia declara
--   cadência e tolerância por daemon, e o limiar é derivado.
--
--   E ALERTAVA A CADA CICLO. Mesmo defeito do P1.4, mesma cura: o silêncio vira
--   EPISÓDIO, com no máximo um aberto por daemon. Vigia a cada 30 min com um daemon
--   morto num fim de semana dava ~100 notificações idênticas; agora dá uma na queda e
--   uma na volta. A volta importa: sem ela o Daniel vê "L2 mudo" e nunca fica sabendo
--   que voltou, a não ser conferindo à mão.
--
-- Fora do escopo desta migration, mas registrado porque é a mesma pergunta: QUEM
-- VIGIA O VIGIA. Se o processo do vigia morre, ninguém alerta — em banda isso é
-- insolúvel por definição. O que dá para fazer aqui é o vigia PULSAR e entrar na
-- própria lista, de modo que a lacuna fique registrada e visível em `vw_saude_daemons`
-- assim que ele voltar. A garantia externa é o systemd (E0.5), não este código.

create table episodios_silencio_daemon (
  id            bigint generated always as identity primary key,
  daemon        text not null,
  aberto_em     timestamptz not null default now(),
  -- Silêncio medido na ABERTURA. NULL = o daemon nunca pulsou (não há de quando
  -- contar) — que é diferente de "silêncio zero" e por isso não vira 0.
  silencio_s    numeric,
  -- Limiar VIGENTE no momento da abertura. Guardado junto porque o roster é
  -- parâmetro operacional e muda: sem isto, um episódio antigo ficaria ilegível
  -- ("por que isso alertou?") depois de qualquer ajuste de cadência.
  limiar_s      numeric not null,
  encerrado_em  timestamptz,
  duracao_s     numeric
);

-- No máximo UM episódio aberto por daemon. É o que torna "um alerta por queda"
-- verificável pelo banco, e não pela sorte de a notificação anterior ainda estar
-- pendente na outbox — exatamente a lição do P1.4.
create unique index ux_episodio_silencio_aberto
  on episodios_silencio_daemon (daemon) where encerrado_em is null;
create index ix_episodio_silencio_daemon
  on episodios_silencio_daemon (daemon, aberto_em desc);

create trigger tg_episodios_silencio_del before delete on episodios_silencio_daemon
  for each row execute function fn_bloqueia_delete();
alter table episodios_silencio_daemon enable row level security;

comment on table episodios_silencio_daemon is
  'P1.5 — um episódio por queda de daemon (não um alerta por ciclo). O índice parcial '
  'garante no máximo um aberto por daemon; o histórico fecha e fica.';

create or replace function fn_abrir_episodio_silencio(
  p_daemon text, p_silencio_s numeric default null,
  p_limiar_s numeric default 0, p_agora timestamptz default now()
) returns jsonb
language plpgsql
set search_path = public, pg_temp
as $$
declare v_id bigint;
begin
  insert into episodios_silencio_daemon (daemon, aberto_em, silencio_s, limiar_s)
  values (p_daemon, p_agora, p_silencio_s, p_limiar_s)
  returning id into v_id;
  return jsonb_build_object('abriu', true, 'episodio_id', v_id, 'daemon', p_daemon);
exception when unique_violation then
  -- Já há episódio aberto para este daemon: é a MESMA queda. Alertar de novo é spam.
  select id into v_id
    from episodios_silencio_daemon
   where daemon = p_daemon and encerrado_em is null;
  return jsonb_build_object('abriu', false, 'episodio_id', v_id, 'daemon', p_daemon,
                            'motivo', 'episodio_ja_aberto');
end $$;

create or replace function fn_encerrar_episodio_silencio(
  p_daemon text, p_agora timestamptz default now()
) returns jsonb
language plpgsql
set search_path = public, pg_temp
as $$
declare v_id bigint; v_aberto timestamptz; v_dur numeric;
begin
  update episodios_silencio_daemon
     set encerrado_em = p_agora,
         duracao_s = extract(epoch from (p_agora - aberto_em))
   where daemon = p_daemon and encerrado_em is null
  returning id, aberto_em, duracao_s into v_id, v_aberto, v_dur;
  -- `encerrou=false` NÃO é erro: é o caso normal de um daemon que estava saudável.
  -- Quem chama usa isso para saber se deve anunciar a VOLTA (só há volta se houve
  -- queda) — sem isso, todo ciclo com todo mundo vivo viraria um alerta de "voltou".
  return jsonb_build_object('encerrou', v_id is not null, 'episodio_id', v_id,
                            'daemon', p_daemon, 'duracao_s', v_dur);
end $$;
