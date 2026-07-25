-- 0014 — "CLV real" passa a exigir ENTREGA EFETIVA (P0.7 da 3ª auditoria).
--
-- PROBLEMA: o L4 classificava como `real` todo sinal com status `confirmado`. Mas um
-- confirmado pode ter sido suprimido pelo L3 (preço fechou ou ficou velho antes do
-- envio), pode nunca ter sido entregue (Telegram fora, outbox presa), pode ter sido
-- entregue DEPOIS do apito — e, em qualquer desses casos, nunca chegou ao Daniel.
--
-- Logo, o "CLV real" media:
--     a qualidade da odd no instante do L1, entre os sinais que o L2 confirmou
-- e NÃO:
--     a qualidade das oportunidades efetivamente recebidas e executáveis.
--
-- A diferença não é semântica: é o KPI que decide a homologação de mercados (E6.4) e
-- a passagem do paper trading para dinheiro real (E7). Medir oportunidade que nunca
-- existiu para o operador infla a evidência que autoriza arriscar dinheiro.
--
-- CATEGORIAS (substituem `real`, que era ambígua):
--   real_entregue            cartão ENTREGUE antes do kickoff — é o KPI do paper trading;
--   confirmado_suprimido     o L2 confirmou, mas o L3 suprimiu (janela fechou/preço velho);
--   confirmado_nao_entregue  falha operacional: nunca enfileirado, preso na outbox,
--                            Telegram fora, ou entregue depois do apito;
--   execucao                 RESERVADA: CLV da odd efetivamente apostada (E5.3),
--                            quando houver execução real a comparar.
--
-- As três primeiras somadas equivalem ao antigo `real` — mas separadas respondem
-- perguntas diferentes: quanto valor o processo GERA, quanto ele PERDE por preço que
-- fugiu, e quanto ele PERDE por falha própria. Somá-las esconderia justamente o custo
-- operacional do pipeline.
--
-- Seguro: `clv_resultados` está VAZIA (0 linhas) — nenhuma classificação histórica
-- precisa ser reinterpretada.

alter table clv_resultados drop constraint clv_resultados_categoria_check;

alter table clv_resultados add constraint clv_resultados_categoria_check
  check (categoria in (
    'real_entregue',            -- entregue antes do kickoff (KPI do paper trading)
    'confirmado_suprimido',     -- confirmado, mas o L3 suprimiu o cartão
    'confirmado_nao_entregue',  -- confirmado, mas a entrega falhou/atrasou
    'execucao',                 -- reservada: CLV da odd realmente apostada
    'contrafactual_l2',         -- vetado pelo crivo
    'contrafactual_l1',         -- near-miss abortado no L1
    'contrafactual_operacional',-- expirado / erro / timeout do crivo
    'calibracao'                -- candidato_sombra (mercado não homologado)
  ));
