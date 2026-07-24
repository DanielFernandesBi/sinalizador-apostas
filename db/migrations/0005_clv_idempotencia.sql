-- 0005 — Idempotência do CLV: um CLV por sinal, um CLV por aborto (achado #2 do 2º ciclo).
--
-- PROBLEMA (o KPI soberano):
--   A não-duplicação do CLV existia SÓ em código: `fechar_evento` lia os ids que já
--   tinham CLV (`clv_ids_registrados`) e pulava os repetidos. Entre essa LEITURA e o
--   INSERT não havia nada — dois processos do L4 (dois terminais, um cron somado a uma
--   execução manual, ou dois ciclos sobrepostos) leem o mesmo conjunto vazio e gravam
--   DOIS `clv_log` para o mesmo sinal.
--
--   `vw_clv_global` é `count(*)`, `avg(clv_pct)` e `stddev(clv_pct)` sobre `clv_log`.
--   Duplicata infla o `n` e distorce a média — e é esse `n` que governa a amostra
--   mínima (200) e a homologação de mercados (E6.4, achado 8). Ou seja: a duplicidade
--   não "suja um relatório", ela CORROMPE o critério que decide em que mercados o
--   sistema pode operar.
--
-- CORREÇÃO:
--   Unicidade no BANCO, que é a única garantia sob concorrência. O índice é parcial
--   porque cada linha preenche exatamente um dos dois lados: `sinal_id` (sinal
--   confirmado/vetado) OU `aborto_l1_id` (near-miss rastreado / candidato_sombra).
--
--   O check do 0001 (`sinal_id is not null or aborto_l1_id is not null`) garante que
--   ao menos um exista; estes índices garantem que cada um apareça NO MÁXIMO uma vez.
--
--   O dedup em código continua — como caminho rápido (evita ida ao banco fadada a
--   falhar), não mais como a garantia. A garantia é aqui.
--
-- Seguro aplicar: `clv_log` está VAZIA (0 linhas) — nenhuma duplicata histórica
-- precisa ser reconciliada antes de criar os índices.

create unique index ux_clv_sinal
  on clv_log (sinal_id)
  where sinal_id is not null;

create unique index ux_clv_aborto
  on clv_log (aborto_l1_id)
  where aborto_l1_id is not null;
