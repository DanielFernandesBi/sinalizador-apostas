-- 0006 — Gate `fechamento_idade_max_s` (Sugestão nº 9, rito de 24/07/2026; achado #3).
--
-- Defasagem máxima ACEITÁVEL entre a revisão completa de fechamento e o início da
-- partida. É o limite que decide se uma revisão completa mais antiga (porque a
-- revisão mais recente estava incompleta — seleção suspensa perto do início) ainda
-- pode ser chamada de "linha de fechamento".
--
-- NÃO reutiliza `snapshot_idade_max_s`: os contratos são diferentes.
--   snapshot_idade_max_s   → frescor necessário para EMITIR um candidato (L1);
--   fechamento_idade_max_s → proximidade mínima aceitável para MEDIR o CLV (L4).
-- Fundi-los amarraria duas decisões independentes ao mesmo número.
--
-- Valor provisório 600 s, coerente com a janela de 10 min já adotada para snapshots,
-- e EXPRESSAMENTE a calibrar (Doutrina §4 — o valor definitivo sai do backtest).
--
-- Não pétreo, direção de endurecimento 'menor' (endurecer = exigir book mais próximo
-- do início). Inscrito na Doutrina §4 (v0.1.6) — a publicação da doutrina no
-- `config_sistema` é feita pelo rito, via `python -m scripts.sync_governanca`.

insert into gates (nome, valor, petreo, direcao_endurecer, versao, motivo) values
  ('fechamento_idade_max_s', 600, false, 'menor', 1,
   'Sugestão nº 9 — defasagem máxima da revisão de fechamento vs. início (L4/CLV) — a calibrar');
