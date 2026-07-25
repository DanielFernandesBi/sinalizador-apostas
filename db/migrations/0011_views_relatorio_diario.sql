-- 0011 — Views do relatório: diário, por categoria e perda de amostra (Tier 3).
--
-- DOIS PROBLEMAS:
--
-- (a) O relatório era ACUMULADO GLOBAL, nunca "o dia". `vw_clv_global` não tem
--     recorte temporal, então "relatório diário" mostrava a série inteira desde o
--     início — e um dia ruim ficava invisível dentro da média histórica.
--
-- (b) `vw_clv_global` agrupa apenas pelo BOOLEANO `contrafactual`. Depois da
--     Sugestão nº 10, três categorias distintas — `contrafactual_l2` (veto do
--     crivo), `contrafactual_l1` (near-miss do L1) e `contrafactual_operacional`
--     (expirado/erro/timeout) — são todas `contrafactual = true` em `clv_log`. A
--     linha "CLV contrafactual" passou a SOMAR as três numa média só, que é
--     precisamente o que a Doutrina §3 (v0.1.7) proíbe: cada categoria responde a
--     uma pergunta diferente (o crivo acerta? o gate de edge está no lugar? o
--     pipeline está perdendo oportunidade?). Somá-las não responde nenhuma.
--
-- `vw_clv_global` fica intacta (compatibilidade e auditoria histórica); as views
-- novas são a base do relatório.
--
-- O dia é o da PARTIDA (`eventos.inicio_utc`), não o do cálculo: é assim que se
-- pergunta "como foi a rodada de ontem", e o L4 pode fechar o CLV horas depois.

create view vw_clv_por_categoria as
select r.categoria,
       count(*)                      as n,
       round(avg(l.clv_pct), 3)      as clv_medio,
       round(stddev(l.clv_pct), 3)   as desvio
from clv_resultados r
join clv_log l on l.id = r.clv_log_id
where r.resultado = 'calculado'
group by r.categoria;

create view vw_clv_diario as
select date(e.inicio_utc)            as dia,
       r.categoria,
       count(*)                      as n,
       round(avg(l.clv_pct), 3)      as clv_medio,
       round(stddev(l.clv_pct), 3)   as desvio
from clv_resultados r
join clv_log l on l.id = r.clv_log_id
join eventos  e on e.id = r.evento_id
where r.resultado = 'calculado'
group by date(e.inicio_utc), r.categoria;

-- Perda de amostra: o que NÃO virou CLV, e por quê. É o que responde "o gate de
-- fechamento está cortando amostra demais?" e "a ausência se concentra em algum
-- mercado?" — perguntas que só existem porque o desfecho agora é registrado
-- (Sugestão nº 10), em vez de sumir em silêncio.
create view vw_clv_perda_amostra as
select date(e.inicio_utc)            as dia,
       r.mercado,
       r.resultado                   as motivo,
       count(*)                      as n
from clv_resultados r
join eventos e on e.id = r.evento_id
where r.resultado <> 'calculado'
group by date(e.inicio_utc), r.mercado, r.resultado;

-- Mesma postura de segurança da 0002: as views respeitam o RLS do chamador e não
-- são legíveis por anon/authenticated (desenho service-role-only).
alter view vw_clv_por_categoria  set (security_invoker = on);
alter view vw_clv_diario         set (security_invoker = on);
alter view vw_clv_perda_amostra  set (security_invoker = on);

revoke all on vw_clv_por_categoria  from anon, authenticated;
revoke all on vw_clv_diario         from anon, authenticated;
revoke all on vw_clv_perda_amostra  from anon, authenticated;
