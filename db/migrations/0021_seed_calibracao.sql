-- 0021 — Seed de CALIBRAÇÃO: o pipeline de evidência liga, sem autorizar nada.
-- (decisão de rito, 26/07/2026 — Sugestão nº 16)
--
-- `mercados_homologados` estava VAZIA, e o gate do L1 é fail-closed: TODO mercado
-- caía em `mercado_nao_configurado` e o sistema não produzia sinal nem
-- candidato_sombra. Não é conservadorismo — é o pipeline de evidência desligado. E
-- como o CLV de sombra é o que homologa (Sugestão nº 16 (c)), enquanto a tabela
-- estiver vazia o sistema não pode nem começar a ganhar o direito de operar.
--
-- A DISTINÇÃO QUE DESTRAVA ISTO: escopo de COLETA ≠ escopo de HOMOLOGAÇÃO. São
-- decisões com perfis de risco opostos.
--
--   * Em `backtest`, o candidato que passa em TODOS os gates vira `candidato_sombra`:
--     é rastreado até o fechamento SÓ para medir CLV, e NUNCA vira sinal, status
--     confirmado ou cartão. O risco de coletar demais é exatamente zero, e mais
--     dado é estritamente melhor — cada célula não semeada é amostra que não começa
--     a acumular hoje e não estará pronta quando a temporada avançar.
--   * `homologado` tem consequência real. Aí sim, estreito: célula por célula,
--     começando pelo 1X2.
--
-- Por isso o seed é LARGO em coleta (6 ligas × 3 mercados, sem limite de linha nem
-- de faixa) e VAZIO em homologação. Nenhuma linha nasce `homologado`.
--
-- Por que 1X2 primeiro, quando chegar a hora — e não pela razão que a auditoria deu:
-- no backtest o 1X2 tem sete casas de varejo disputando o melhor preço, enquanto OU
-- e AH degeneram para o Bet365 sozinho. A evidência de 1X2 é a menos contaminada
-- pelo problema do venue histórico (PC-VENUE-HISTORICO), então é a primeira que
-- merece confiança. OU e AH continuam COLETANDO desde já — só demoram mais para
-- serem promovidos.
--
-- As ligas vêm do contrato único `sinalizador/comum/ligas.py`; os rótulos abaixo são
-- os mesmos que o L0 grava em `eventos.liga` (é a chave da homologação). Divergir
-- daqui é reintroduzir o defeito que a 0020 corrigiu.

insert into mercados_homologados (liga, mercado, status, motivo)
select liga, mercado, 'backtest',
       'seed de calibração (Sugestão nº 16): coleta de CLV de sombra. NÃO autoriza '
       'sinal — candidato que passa nos gates vira candidato_sombra e é medido até o '
       'fechamento. Promoção a homologado é por CÉLULA, com P12 + IC95 > 0.'
  from (values
    ('Premier League'), ('La Liga'), ('Serie A'),
    ('Bundesliga'), ('Ligue 1'), ('Primeira Liga')
  ) as l(liga)
  cross join (values ('1x2'), ('ah'), ('ou')) as m(mercado)
on conflict do nothing;

-- Tripwire de intenção: se alguma linha nascer 'homologado' por engano, isto falha e
-- a migration não aplica. O seed autoriza medição, nunca operação.
do $$
declare n int;
begin
  select count(*) into n from mercados_homologados where status = 'homologado';
  if n > 0 then
    raise exception 'seed de calibração não pode deixar % célula(s) homologada(s)', n;
  end if;
  select count(*) into n from mercados_homologados where status = 'backtest';
  if n <> 18 then
    raise exception 'esperadas 18 células de calibração (6 ligas × 3 mercados), há %', n;
  end if;
end $$;
