-- 0022 — Brasileirão Série A entra na calibração (decisão D6, 26/07/2026).
--
-- O sistema não tinha NENHUM evento dentro do horizonte de captura: as seis ligas
-- europeias só começam em 15/08, e cada dia parado é amostra que não se recupera —
-- preço não se captura retroativamente. O Brasileirão está em temporada AGORA.
--
-- Esta liga NÃO tem histórico no Football-Data, e isso é declarado em
-- `comum/ligas.py` (`div=None` → `SEM_HISTORICO`). Pela Sugestão nº 16 (c) isso é
-- permitido e não é uma exceção: ausência de backtest NÃO é veto — o backtest só
-- VETA, e quem homologa é o CLV de sombra, medido no venue real. Uma célula sem
-- histórico simplesmente nunca passa pelo veto; segue direto para a sombra.
--
-- Série B fica de fora DE PROPÓSITO: não se amplia liga e mercado ao mesmo tempo,
-- senão um resultado ruim não tem a quem ser atribuído.
--
-- RISCO A ACOMPANHAR, não resolvido aqui: a referência é a Pinnacle de-vigada. Se a
-- Pinnacle não cobrir o Brasileirão na região `eu`, a arquitetura de referência não
-- se aplica a esta liga e as células ficarão mudas por falta de book — o gate de
-- cobertura (E1 aceite #1) é quem vai dizer, na primeira captura.

insert into mercados_homologados (liga, mercado, status, motivo)
select 'Brasileirão Série A', m.mercado, 'backtest',
       'seed de calibração (D6, 26/07/2026): liga SEM histórico no Football-Data. '
       'Nunca será vetada por backtest (não há de onde) e só pode ser homologada '
       'pelo CLV de sombra — Sugestão nº 16 (c).'
  from (values ('1x2'), ('ah'), ('ou')) as m(mercado)
on conflict do nothing;

do $$
declare n int;
begin
  select count(*) into n from mercados_homologados where status = 'homologado';
  if n > 0 then
    raise exception 'seed de calibração não pode deixar % célula(s) homologada(s)', n;
  end if;
  select count(*) into n from mercados_homologados where liga = 'Brasileirão Série A';
  if n <> 3 then raise exception 'esperadas 3 células do Brasileirão, há %', n; end if;
end $$;
