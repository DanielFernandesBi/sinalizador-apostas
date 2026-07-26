-- 0020 — A homologação passa a representar a MESMA célula que o backtest avalia.
-- (auditoria "5. Backtest e homologação: o contrato ainda não fecha")
--
-- O backtest conclui por LIGA × MERCADO × LINHA × FAIXA DE ODD. A homologação sabia
-- dizer apenas LIGA × MERCADO. Duas consequências, opostas e ambas ruins:
--
--   1. Se uma faixa de odd tem CLV positivo e outra negativo, homologar o mercado
--      inteiro AUTORIZA a faixa ruim junto com a boa — a autorização afirma mais do
--      que a evidência sustenta, que é exatamente o que P2 existe para impedir.
--   2. Quem não quer autorizar a faixa ruim é obrigado a não homologar nada — a
--      evidência boa fica sem uso. O sistema fica mudo onde tinha prova.
--
--   E em OU/AH não havia sequer como limitar por LINHA: homologar 'ou' autorizava
--   toda linha de gols, embora o backtest só tenha medido a 2.5.
--
-- A cura é a linha da tabela representar uma CÉLULA: (liga, mercado, linha, faixa de
-- odd). Os limites são OPCIONAIS e, quando NULL, significam "qualquer" — então uma
-- linha (liga, mercado, NULL, NULL, NULL) continua valendo exatamente como antes, e
-- nada do que já existe muda de sentido. `mercados_homologados` está VAZIA hoje, o
-- que torna a mudança de forma barata; fazê-la depois de semeada seria caro.
--
-- ESCOPO DELIBERADAMENTE FORA: esta migration NÃO decide se homologar por célula ou
-- por mercado, nem qual critério estatístico autoriza. Ela só faz o banco CONSEGUIR
-- representar a decisão. O critério é rito (PC-SIGNIFICANCIA, PC-GRANULARIDADE).

alter table mercados_homologados add column linha numeric;
alter table mercados_homologados add column odd_min numeric;
alter table mercados_homologados add column odd_max numeric;

comment on column mercados_homologados.linha is
  'Linha canônica do mercado (OU 2.5, AH -0.5). NULL = qualquer linha. O backtest '
  'só mediu OU na 2.5: homologar "ou" com linha NULL autoriza linha que ninguém mediu.';
comment on column mercados_homologados.odd_min is
  'Piso da faixa de odd do VENUE, inclusivo. NULL = sem piso.';
comment on column mercados_homologados.odd_max is
  'Teto da faixa de odd do VENUE, EXCLUSIVO — mesma convenção de backtest.FAIXAS, '
  'para que [1.50, 2.00) e [2.00, 2.60) não se sobreponham em 2.00. NULL = sem teto.';

-- Faixa coerente. Sem isto, (odd_min 3.0, odd_max 2.0) seria aceita e casaria com
-- odd nenhuma — uma homologação que existe e nunca se aplica, pior que a ausência
-- (a ausência é fail-loud; esta seria muda).
alter table mercados_homologados add constraint ck_homolog_faixa_coerente
  check (odd_min is null or odd_max is null or odd_min < odd_max);

-- A unicidade antiga era (liga, mercado) e agora impediria a própria granularidade.
-- `nulls not distinct` (PG 15+) é o ponto: sem ele, duas linhas (L, '1x2', NULL,
-- NULL, NULL) seriam ambas aceitas — NULL != NULL — e a tabela teria duas regras
-- conflitantes para a mesma célula, com o desempate saindo da ordem de leitura.
alter table mercados_homologados drop constraint mercados_homologados_liga_mercado_key;
create unique index ux_homolog_celula
  on mercados_homologados (liga, mercado, linha, odd_min, odd_max) nulls not distinct;

-- Resolução da célula: a linha MAIS ESPECÍFICA que cobre (liga, mercado, linha, odd).
-- Especificidade = quantos limites a linha crava. Assim "1x2 inteiro em backtest, mas
-- a faixa 2.00-2.60 homologada" é expressável, e a faixa vence o geral.
create or replace function fn_status_homologacao(
  p_liga text, p_mercado text, p_linha numeric default null, p_odd numeric default null
) returns text
language sql
stable
set search_path = public, pg_temp
as $$
  select case when m.suspenso_em is not null then 'suspenso' else m.status end
    from mercados_homologados m
   where m.liga = p_liga
     and m.mercado = p_mercado
     and (m.linha is null or (p_linha is not null and m.linha = p_linha))
     and (m.odd_min is null or (p_odd is not null and p_odd >= m.odd_min))
     and (m.odd_max is null or (p_odd is not null and p_odd <  m.odd_max))
   order by (m.linha is not null)::int + (m.odd_min is not null)::int
          + (m.odd_max is not null)::int desc,
          m.id
   limit 1;
$$;

comment on function fn_status_homologacao is
  'Status da célula mais específica que cobre (liga, mercado, linha, odd). NULL = '
  'nenhuma linha cobre — ausência de homologação, que o L1 trata como falha de '
  'configuração (P2 não autoriza calibração implícita).';
