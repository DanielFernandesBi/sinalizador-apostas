# AUDITORIA COMPLETA — 3º CICLO (25/07/2026)

**Natureza:** reanálise integral do sistema no estado atual (repo `e72179e` + Supabase `jxveebxywadyxuhixcxt`), conduzida como primeira auditoria — sem presumir a validade das correções dos ciclos anteriores. Cinco frentes independentes (L0, L1, L2, L3/L4, comum/migrations/infra), cada achado verificado no código antes de reportado.

---

## 1. Estado verificado do sistema (fatos, não opinião)

| Verificação | Resultado |
|---|---|
| Suíte de testes (lock, venv limpo) | **307 verdes** em 0,96 s |
| Governança repo ↔ banco | **Em dia**: md5 de `docs/doutrina_v0.1.md` = doutrina v8 vigente (`7d851331…`); md5 do manual = manual v3 vigente (`d45a67d0…`) |
| Gates vigentes | **18/18**, valores conferidos; 4 pétreos corretos |
| Migrations aplicadas | 11/11, espelhadas no repo |
| Advisors Supabase (security + performance) | **Zero ERROR/WARN** — só INFO (RLS sem policy = fail-closed deliberado; índices "não usados" = sistema mal rodou) |
| Triggers de imutabilidade | Presentes e ativos em todas as tabelas do desenho (verificado no catálogo vivo) |
| `ts_fonte` ≠ `ts_captura` | **3.984/3.984 snapshots** (100%) — carimbo de fonte disciplinado |
| Captura real | Só 21/07 (~12h, 61 eventos, 5 ligas). **O aceite de 48h contínuas do E1 nunca foi cumprido.** Daemons mudos desde 21–22/07 |
| Sinais/abortos/notificações/CLV | **Zero linhas** — o sistema nunca emitiu nada |
| `mercados_homologados` | **Vazia** → fail-closed total (`mercado_nao_configurado`) |
| `config_sistema.venues_executaveis` | **Ausente** → fail-closed total no modo sombra |
| Eventos capturados | Bundesliga (11), La Liga (15), Ligue 1 (9), Premier League (10), Serie A (16) — **primeiro kickoff 15/08/2026** (Portugal/P1 ainda não apareceu na janela D+2) |

---

## 2. Achados CRÍTICOS (Tier 0 — corrigir antes de ligar)

### C1. Truncagem silenciosa de 1000 linhas do PostgREST corrompe L1 e a linha de fechamento do L4
`comum/db.py:146-155` (`snapshots_desde`), `db.py:342-353` (`snapshots_do_evento`); expostos em menor grau: `chaves_abortos_desde`, `chaves_sinais_abertos`, `exposicao_aberta`, `homologacao_mercados`, `sinais_do_evento`, `clv_ids_registrados`.
O Supabase hospedado corta em `db-max-rows = 1000` **sem erro**; o supabase-py não pagina. `snapshots_desde` (janela de 1h do L1) ordena **ascendente** → acima de 1000 linhas/h, o L1 perde exatamente as linhas **mais novas** e decide sobre retrato defasado. `snapshots_do_evento` é a base da revisão de fechamento → acima de 1000 snapshots pré-kickoff, o CLV é medido contra book antigo, silenciosamente. Escala real facilmente ultrapassa: ~20 casas × 3 mercados × ~7 outcomes × N jogos × 12 ciclos/h. **É o defeito que só aparece quando o sistema começa a funcionar de verdade — e corrompe o KPI soberano sem sintoma.**
*Correção:* paginação com `.range()` até esgotar, ou asserção fail-loud `len(data) < 1000` em toda leitura de volume.

### C2. Fila de notificação trata `confirmado` como transitório: starvation após o 200º sinal + flood de supressões
`comum/db.py:251-261` + `l3_notifica/notifica.py:117-134`.
(a) `sinais_por_status("confirmado", 200)` ordena por `criado_em` **ascendente** com `limit 200`, e `confirmado` é status terminal (nunca sai do conjunto). O filtro "sem cartão" é em Python, pós-paginação: a partir do 201º confirmado da vida do sistema, a página devolve sempre os 200 mais antigos e **o sinal novo nunca é notificado** — perda silenciosa da função-fim do sistema.
(b) Confirmado com janela fechada é re-suprimido **a cada ciclo** (o registro de supressão é `administrativo`, o caminho rápido só olha `tipo='sinal'`): ~2.880 linhas `interno`/dia por sinal suprimido, para sempre.
*Correção:* recorte terminal no SQL (excluir sinais com cartão/supressão registrada; ou recortar por kickoff futuro).

### C3. L2: gravação do crivo e transição de status fora do try/except — fila bloqueável, custo de modelo repetido, sem alerta
`l2_crivo/crivo.py:144-159`.
O invariante "qualquer falha → `erro` + alerta" cobre só as linhas 127-141. Exceção no `inserir("crivos")` ou na transição (rede, unique de `crivos.sinal_id`, `EstadoInesperadoError`) propaga, derruba o `for` de `processar_fila`, não pulsa heartbeat e não gera alerta. Pior cenário: processo morre entre o insert do crivo e a transição → o sinal segue `aguardando_crivo` na **cabeça da fila** (`order by criado_em`), o modelo é chamado de novo a cada ciclo (custo repetido), o insert viola o unique, o ciclo aborta — **a fila inteira do L2 fica bloqueada** até o kickoff daquele evento.
*Correção:* insert+transição atômicos numa função SQL (padrão da 0004) + try que chame `_registrar_erro`, tratando unicidade/`EstadoInesperadoError` como casos esperados.

### C4. (DESENHO) A calibração é inalcançável no estado atual: o nó das duas tabelas vazias é um nó de três pontas
`l1_gatilhos/orquestrador.py:248-254` (seleção de venue executável) vs. `:327` (desvio candidato_sombra).
A escolha do venue executável acontece **antes** do desvio para `candidato_sombra` e **antes** dos abortos near-miss: sem `venues_executaveis`, o grupo é pulado (`nenhum executável (allowlist)`) e **nem o regime de calibração do achado 8 registra nada** — zero candidatos_sombra, zero near-miss, zero CLV. E a terceira ponta: **não existe allowlist honesta possível hoje** — as 21 casas de varejo capturadas são todas europeias (The Odds API não tem região `br`); semear a allowlist com chaves .bet.br é inerte (nunca capturadas), semear com casas EU é semanticamente falso ("executável" onde o Daniel não executa). Logo, semear `mercados_homologados` sozinha **não destrava nada**. Ver §7 para a proposta de resolução pelo rito.

---

## 3. Achados ALTOS (Tier 1)

### A1. A proteção dos gates pétreos no banco é vazia; a do app não revalida
- `db/migrations/0001:58-74` + `:322-341`: `tg_gate_endurece` é `BEFORE INSERT` apenas e `gates` **não** está na lista de UPDATE bloqueado → `update gates set valor=10 where nome='stake_max_pct' and vigente` afrouxa um pétreo sem tocar trigger. No fluxo real de nova versão (desvigorar → inserir), o trigger não encontra vigente e não valida nada.
- `comum/gates.py:151-175`: `validar_integridade()` (tripwire) roda só na subida; a recarga por TTL (30 s) reidrata sem checar. Um pétreo adulterado entra em decisão de sizing em ≤30 s, sem alarme até o próximo restart. `comum/gates.py` é o único módulo central **sem nenhum teste**.
*Correção:* trigger `BEFORE UPDATE` em `gates` com whitelist (`vigente` apenas) + validação de endurecimento contra `max(versao)` anterior (migration, via rito) + revalidar integridade na recarga do TTL.

### A2. Gate de exposição é quase inoperante
`orquestrador.py:679-689` + `0001:355-362` + `:558-569`.
(a) `vw_exposicao_aberta` soma só `apostas` pendentes (já executadas pelo humano) — sinais `aguardando_crivo`/`confirmado` não contam; no modo sombra (nenhuma aposta real) a exposição é perpetuamente 0. (b) O dict `exposto` é lido uma vez por ciclo e nunca incrementado após cada `enfileirar_sinal` — duas seleções do mesmo jogo estouram o teto juntas no mesmo ciclo. (c) A chave "dia" compara data de **início do evento** (orquestrador) com data de **execução da aposta** (view) — grouping sets nunca casam para jogos de amanhã; no nível "jogo", atribuição sobrescreve em vez de somar. (d) O dossiê grava `gates_exposicao_ok: True` hard-coded no caminho aprovado.

### A3. Candidato vetado/erro é reemitido a cada ciclo — loop L1→L2 de custo LLM
`0003:17-19` + `db.py:181-191` + `orquestrador.py:260-263`.
O índice parcial e o dedup cobrem só `aguardando_crivo|confirmado`. Pós-veto, o candidato sai do índice; a mesma oportunidade estável re-passa os gates a cada 60 s → novo sinal → nova chamada ao crivo → novo veto. Odd estável por 2h = ~120 sinais + 120 execuções do modelo para o mesmo candidato. (Reemissão pós-`expirado` é defensável; pós-`vetado` no mesmo estado de mundo, não.)

### A4. Fila do L2 sem reserva atômica
`0008` (`fn_fila_crivo` é SELECT puro, sem `for update skip locked`) vs. o padrão correto já existente no L3 (`fn_reivindicar_notificacoes`, 0010). Dois processos L2 (ou ciclos sobrepostos) pagam o modelo duas vezes pelo mesmo sinal; o perdedor esbarra no unique e — pelo C3 — derruba o próprio ciclo. A integridade do veredicto se mantém (transição atômica); o dano é custo + bloqueio.

### A5. `tools=None` no corpo da requisição do caminho rápido do L2
`l2_crivo/modelo.py:81`: `tools=tools or None` serializa `"tools": null` (o SDK usa `NOT_GIVEN` para omissão). Risco real de HTTP 400 em **toda** chamada do caminho rápido (o caso comum) → todo sinal viraria `erro` (fail-safe na direção certa, mas L2 inutilizado). `ModeloAnthropic` não tem nenhum teste.
*Correção:* kwargs condicional (`if tools: kwargs["tools"] = tools`).

### A6. Falha do calendário suspende a captura silenciosamente por até 1h+
`l0_captura/cadencia.py:120-123` + `cli.py:88-95`: erro transitório no `/events` grava `[]` por sport, o cache é substituído inteiro e `ultimo_calendario` avança mesmo com falha total → "não há jogo à vista" → daemon dorme `base_s` (3600 s) em cima do kickoff. Falha de fonte convertida em dado (contra a regra 3). O teste `test_ler_calendario_sport_que_falha_vira_vazio` consagra o comportamento errado.
*Correção:* manter calendário stale por sport que falhou; não avançar o relógio de refresh em falha.

### A7. Monitoramento com falso negativo estrutural e spam
- `l0_captura/captura.py:104-123` + `vigia.py:30-48`: ciclo com **100% dos sports falhando** (chave revogada, cota estourada) ainda pulsa heartbeat → o vigia (que só olha silêncio) considera tudo saudável **para sempre**. Cegueira total sem alerta.
- `vigia.py:55-66` + `0010:44-46`: `alerta_daemon` não tem chave idempotente → daemon morto num fim de semana = ~96 alertas até segunda (canal vira ruído). O anti-spam do alerta de drawdown (`notifica.py:90-105`) só olha pendentes → com kill switch ativo, um "⛔ KILL SWITCH" a cada ciclo de 30 s. E ninguém vigia o vigia (sem heartbeat próprio).

### A8. AH/OU sem `point` vira snapshot com `linha=NULL` — candidato sem sentido apostável
`l0_captura/mapeamento.py:110-126, 155-179`: outcome de spreads/totals sem `point` é persistido com `linha=NULL` e forma grupo próprio no L1 (`(ev,'ah',None)`); se os dois lados vierem sem point, o book "fecha" e pode emitir candidato de OU/AH **sem linha**. Viola P6 — `point` ausente deveria descartar o outcome com warning, como já se faz com `ts_fonte`.

### A9. Jogo remarcado: `eventos.inicio_utc` nunca é atualizado nem sequer conferido
`l0_captura/persistencia.py:27-38`: `garantir_evento` ignora o `commence_time` novo quando o evento já existe. Jogo adiado 26h → o L4 dispara o fechamento no horário velho e toma como closing line um snapshot ~26h antes do kickoff real → **CLV silenciosamente corrompido**. Como `eventos` só admite update de `status` (por desenho), a correção pede decisão de governança — mas hoje a divergência nem é detectada/logada.

### A10. Nenhum freio de crédito da The Odds API; cadência é global
`captura.py:80-85` + `cadencia.py:89-107`: `requests_remaining` é só logado, nunca decide nada; um único jogo a ≤1h põe TODAS as ligas ativas em cadência de 5 min → ~216 créditos/h num sábado; o tier gratuito (500/mês) morre em ~2,5h e o pós-estouro é o cenário A7 (cegueira sem alerta).
*Correção:* intervalo por sport + guarda mínima de `requests_remaining` (abaixo de N → degradar para base + alertar).

### A11. `fn_finalizar_evento_clv` ignora `aguardando_crivo` → item de CLV perdido permanentemente
`0007:189-198`: itens esperados = `confirmado|vetado|expirado|erro|timeout_crivo`; sinal ainda `aguardando_crivo` no momento da finalização não conta como pendente. Como o L1 não tem guarda de kickoff na emissão e as janelas coincidem (600 s + 600 s), um sinal tardio pode nascer após o `marcar_timeout_crivo` do ciclo e o evento finaliza sem ele; `fn_fila_fechamento` o exclui para sempre — exatamente a perda permanente e silenciosa que a 0007 veio eliminar.
*Correção:* incluir `aguardando_crivo` na contagem de pendentes (migration) e/ou guarda de kickoff na emissão do L1.

### A12. `fn_registrar_aposta` permite N apostas (N débitos) para o mesmo sinal
`0004:72-115`: a idempotência é por `aposta_id` — cada chamada cria aposta nova; duplo clique/retry da CLI humana = débito em dobro (irreparável por P7 sem `ajuste_formal`). Também não verifica que o sinal está `confirmado`.
*Correção:* índice único parcial `apostas(sinal_id) where resultado='pendente'` + checagem de status (migration).

---

## 4. Achados MÉDIOS (Tier 2)

| # | Onde | Defeito |
|---|---|---|
| M1 | `orquestrador.py:211-219` | De-vig mistura seleções de timestamps distintos (última odd de cada seleção, sem sincronia mútua intra-referência) → `p_justa` de book quimérico; sincronia/idade só valem para a seleção escolhida. Edge fantasma na dimensão que a Sugestão nº 9 fechou no L4 mas não no L1. |
| M2 | `motor_gates.py:107-123` | Kelly usa odd bruta enquanto o edge desconta comissão → overstake ~3× em venue de exchange (abaixo do teto, que não corta). Varejo (c=0) não afetado. |
| M3 | `gatilhos.py:36-45` | `variacao_pct` com <2 pontos retorna 0 → "referência estável" e "sem anomalia" por vacuidade (ausência de dado virando aprovação de gate). |
| M4 | `l2_crivo/crivo.py:69-79` | Validação estrita aceita saídas incoerentes: CONFIRMA com `motivo_veto`/fator `veto`; ABORTA sem motivo; `caminho_executado` divergente do dossiê. |
| M5 | `crivo.py:76-78,106-110,140` | Conteúdo controlado pelo modelo (sinal_id arbitrário, `str(e)` de SDK) entra sem sanitização/truncamento no alerta administrativo que vai ao Telegram (regra 8). |
| M6 | `modelo.py:27-30,47-50` | Preços de token hard-coded, desacoplados do parâmetro `modelo`, cegos ao custo da busca web (caminho caro subestimado). Regra 6 na fronteira. |
| M7 | `modelo.py:56,75-85` | `max_tokens=4096` aperta o caminho profundo (thinking+texto); `stop_reason` `pause_turn`/`refusal`/`max_tokens` não tratados → erro crônico com motivo genérico. Nunca vira CONFIRMA (ok), mas queima sinais bons. |
| M8 | `0007:252-267` + `crivo.py:144` | Corrida CONFIRMA × `fn_timeout_crivo`: crivo gravado para sinal que já virou `timeout_crivo` — `crivo_do_sinal` devolve veredicto que nunca valeu; a exceção cai no C3. |
| M9 | `notifica.py:157` + `bot.py:25` | Reclaim da outbox (300 s) < pior duração de lote (200 × 15 s ≈ 50 min) → sob degradação do Telegram com dois remetentes, duplicatas **sistemáticas**, não raras. |
| M10 | `notifica.py:155-177` | Retry tardio da outbox entrega cartão sem re-checagem de preço (Telegram fora por 2h → cartão velho na volta). Mitigado pela trava da odd mínima no texto. Falta teto de idade da pendente. |
| M11 | `notifica.py:79-86` | `expirar_pendentes` trata QUALQUER exceção como corrida benigna com o L2 (log info) — falha de infra fica invisível. |
| M12 | `comum/erros.py:14-19` | Classificação de unicidade por substring da mensagem inteira (que carrega valores de linha derivados de texto externo) — erro real pode ser engolido como corrida benigna. Privilegiar `code == '23505'`. |
| M13 | `0001:322-341` (notificacoes) | Nenhum trigger de UPDATE em `notificacoes`: `conteudo`/`tipo`/`sinal_id` reescrevíveis — registro de auditoria do que foi enviado é adulterável. `fn_marcar_notificacao_entregue` aceita `pendente→entregue` direto. |
| M14 | `eventos`/`config_sistema` | Furos de imutabilidade: `eventos.inicio_utc`/`ids_externos` mutáveis sem trilha; `config_sistema.valor` da doutrina **vigente** reescrevível in-place (o versionamento não é garantido pelo banco). Whitelist de coluna fecharia. |
| M15 | `0004:50-59` | `fn_apostas_update` permite liquidar mudando só `resultado` (sem payout/ledger) — o "ou tudo ou nada" está na disciplina de usar a RPC, não no banco. |
| M16 | `mapeamento.py:84-107` | Evento sem `home_team` + outcome sem `name` → seleção inventada ("1"/"mandante") por match de strings vazias. |
| M17 | `the_odds_api.py:83-96` | `TimeoutError`/`IncompleteRead`/`ConnectionResetError` no `read()` escapam de `OddsAPIError` → derrubam o ciclo inteiro e perdem os sports já capturados (créditos gastos). |
| M18 | `captura.py:87-107` | Re-inserção do mesmo `ts_fonte` a cada tick sem dedup — volume da maior tabela cresce em ordem de grandeza acima do necessário (custo, não corrupção). |
| M19 | `mapeamento.py:51` | `COMISSAO_EXCHANGE_PCT = 6.5` hard-coded (regra 6) — só semeia a criação da casa, mas o valor de negócio nasce no código. |
| M20 | `0001:87-99` | `eventos` sem índice (nem unique) em `ids_externos->>'odds_api'`: get-or-create com TOCTOU (evento duplicado divide snapshots e o book nunca fecha — latente) + lookup mais quente do L0 em seq scan. |
| M21 | `parser_tips.py:72-77,92-96` | Linha do over/under não extraída com palavras PT ("mais de 2,5" → `linha=None`, `interpretavel=True`); fallback de odd pega o último decimal ("1.75 unidades 5.5" → odd=5,5). Contamina `tips.interpretacao` e o futuro ranking. |
| M22 | `db.py:596-621` | `publicar_config` sem lock: corrida deixa zero versões vigentes (fail-closed a jusante, mas o L2 para até re-sync). |

## 5. Achados BAIXOS e observações (Tier 3 — seleção)

- `orquestrador.py:204`: status `"calibracao"` aceito no código mas inexistente no CHECK do schema (código morto/armadilha).
- `orquestrador.py:215,221,254`: skips por P6 (referência incompleta, devig falhou — inclusive booksum<1, sem venue) só em memória — categoria "não avaliável" invisível pós-ciclo (P7).
- `devig.py`: book absurdo (overround 98%) de-viga sem sanidade de booksum/z (gate de edge protege a jusante).
- `cartao.py:96`: crivo ausente → cartão imprime "CONFIRMA" por default (dado ausente virando valor típico impresso).
- `clv.py:387-396`: finalização que falha por rede após todos os desfechos nunca é retentada (`clv_eventos_finalizados` fica sem a linha; nenhum CLV se perde).
- `clv.py:265`: `p_emissao` de aborto sem `p_justa` cai na prob implícita COM vig, indistinguível depois.
- `clv.py:150-155`: odds inválidas na revisão viram motivo `sem_revisao_completa` (motivo mente para a análise de perda).
- `cli.py` L4 + `0011:34-44`: "dia" é a data UTC do kickoff — rodada de 21h BRT cai no "hoje" de amanhã; não documentado no relatório.
- `notifica.py:157,181` / `relatorio.py:99`: `reclaim_s=300`, limite 500 do anti-spam e `limiar_silencio_s=3600` hard-coded; o limiar de daemon mudo existe em dois lugares.
- `notifica.py:180-181`: `agora_iso=None` repassado à RPC deixaria linha `enviando` presa para sempre (arma engatilhada na assinatura).
- `crivo.py:126`: `dossie.get("caminho", "rapido")` — default silencioso para o caminho de MENOR escrutínio.
- `crivo.py:40-45`: manual ausente levanta `SaidaInvalidaError` (tipo errado) e sem alerta administrativo.
- `crivo.py:48-66`: extração de JSON pega a primeira cerca — resposta que ecoe o dossiê antes da saída falha (robustez, não segurança).
- `modelos.py:174-186`: `Fator.id`/`MotivoVeto.id` strings livres (domínio do Manual não travado).
- `db.py:518-522`: `inserir("banca_ledger")` genérico permite aporte manual fora do lock da 0004 (TOCTOU reintroduzível; falta `fn_lancar_aporte`).
- `db.py:19,29,328-340,460-463`: `marcar_evento_encerrado`/`eventos_iniciados_sem_status_final` são código morto desde a 0007, mas o docstring do módulo os apresenta como fluxo vivo.
- GRANTs de tabela a `anon`/`authenticated` nunca revogados (RLS-sem-policy é a única camada; belt-and-suspenders recomendado).
- `config.py:41-45,61-63`: `.env` resolvido pelo cwd (systemd com WorkingDirectory errado perde o `.env` silenciosamente); URL sem validação.
- `ci.yml`: actions por tag mutável (não SHA); lock sem `--hash`; lock gerado em 3.11 (mitigado: CI valida em 3.12).
- `cadencia.py:44-54`: `parse_kickoff` duplica `tempo.para_datetime` linha a linha.
- `mapeamento.py:45` / `cobertura.py:23`: `CASA_REFERENCIA` duplicada em dois módulos.
- `mapeamento.py:72-81` + `persistencia.py`: evento sem `commence_time` explode no NOT NULL e derruba o ciclo, em vez de ser descartado com warning.
- `cobertura.py:53-71`: nomes de times crus via `print` no stdout do log JSON (regra 8, CLI manual).
- Casas duplicadas no banco: `betfair_ex_eu` E `betfair_exchange` (uma é semente da 0001, outra criada em runtime) — inofensivo, mas vale higiene.

## 6. O que está sólido (verificado, não presumido)

- **Nenhum caminho encontrado, em nenhuma camada, em que falha, corrida ou injeção produza sinal/CONFIRMA/notificação indevidos.** Os invariantes centrais seguram: falha → erro/aborto; a assimetria do L2 é estrutural (a odd do crivo nem é persistida); transição de status é atômica e espelhada por trigger; `crivos.sinal_id` é UNIQUE.
- Service role só em `comum/db.py` (grep completo); nenhuma chamada a LLM fora de `l2_crivo/`; varredura de segredos limpa (código + histórico git); `.env` com precedência correta e testada.
- `ts_fonte` disciplinado de ponta a ponta (100% dos snapshots reais ≠ `ts_captura`; nenhum caminho cai em `now()`).
- Linha canônica do AH correta nos dois lados; agrupamento do L1 e revisão indivisível do L4 coerentes entre si; fechamento nunca mistura casas/revisões; completo por LINHA em AH/OU.
- Shin: bracketing provado, casos publicados batem, degenerados corretos; `odd_minima_aceitavel` é a inversão exata da fronteira do gate (ponto fixo testado).
- Contabilidade (0004): advisory lock + `for update`, payout derivado, Decimal ponta a ponta, 5 resultados testados contra o banco real.
- Outbox (0010): `skip locked`, cartão único por sinal, linha antes do envio, contador honesto, `parse_mode` ausente (nomes de time maliciosos inertes no Telegram), token jamais logado.
- CLV: categorias corretas e nunca somadas (confirmado→real, vetado→contrafactual_l2, near-miss→l1, expirado/erro/timeout→operacional, não-homologado→calibracao); `fn_registrar_resultado_clv` idempotente com rollback de subtransação; falha de infra deixa item pendente.
- Homologação fail-closed nos quatro caminhos, com testes; allowlist fail-closed com testes; kill switch duro antes do sizing; banca de papel sem tocar o ledger real.
- Backtest sem look-ahead; decomposição de linhas de quarto do AH correta (inclusive visitante).
- CI sem segredos, portão pelo lock em 3.12; `test_migrations.py` amarra gates↔seeds nos dois sentidos.

## 7. A pendência das duas tabelas vazias — análise e proposta

**O que a pendência parecia ser:** "semear duas tabelas". **O que ela é:** um nó de três pontas (achado C4):

1. `mercados_homologados` vazia → tudo `mercado_nao_configurado` (fail-closed correto);
2. `venues_executaveis` ausente → nenhum venue de cartão (fail-closed correto); **mas a checagem de executabilidade vem ANTES do desvio de calibração**, então também: zero `candidato_sombra`, zero near-miss, zero CLV;
3. não existe allowlist honesta possível: as casas capturadas são todas europeias (The Odds API não tem região `br`). Semear chaves .bet.br é inerte; semear casas EU é falsear "executável".

**Consequência:** no estado atual, mesmo semeando `mercados_homologados`, o sistema continua estatisticamente mudo — a calibração do E6.4 (amostra ≥200 por célula para homologar) nunca começa, e o E7 nunca chega.

**Proposta (exige rito — registrar como PC-CALIBRACAO-SEM-VENUE ou similar):**
Distinguir *medição* de *execução*. A executabilidade é um requisito do **cartão** (não se sinaliza o que não se pode apostar); não é um requisito da **medição de CLV** (o candidato_sombra e o near-miss nunca viram cartão — são régua, não aposta). Mudança cirúrgica no `avaliar_grupo`: quando o destino é calibração/aborto (nunca sinal), o venue de medição é o melhor preço de varejo **capturado** (allowlist ignorada, com marcador explícito ex.: `venue_medicao=true` no dossiê parcial); a allowlist continua obrigatória e fail-closed para o caminho `homologado` → sinal → cartão. Assim:
- a calibração começa já na 1ª rodada de agosto, com as casas EU como régua de CLV;
- o significado de "executável" fica intacto;
- quando a fonte BR existir (PC-VENUE) e o Daniel semear a D3, os sinais homologáveis nascem por cima de uma curva de calibração já em andamento.

**Semeadura de `mercados_homologados` (decisão sua; SQL pronto, não aplicado):**

```sql
insert into mercados_homologados (liga, mercado, status, motivo)
select l, m, 'backtest',
       'Semeadura inicial do regime de calibração (rito 24/07/2026, achado 8; auditoria 3º ciclo)'
from (values ('Premier League'),('La Liga'),('Serie A'),('Bundesliga'),('Ligue 1')) as ligas(l)
cross join (values ('1x2'),('ah'),('ou')) as mercados(m)
on conflict (liga, mercado) do nothing;
```

Notas: os cinco nomes de liga são exatamente os strings de `eventos.liga` capturados; Portugal (P1) ainda não apareceu na janela D+2 — quando aparecer, o marcador fail-loud `mercado_nao_configurado` avisará, e aí se adiciona a linha com o string real (comportamento correto do desenho, não defeito). `venues_executaveis`: recomendo **não semear nada** até a fonte BR — com a proposta acima, a ausência dela deixa de silenciar a calibração e volta a significar só o que deve: "nenhum cartão".

## 8. Ligar o sistema agora? Avaliação estratégica

**Ligar hoje, como está, entrega quase nada e arrisca o pouco que entrega.** Concretamente:
- Zero sinais e zero CLV (nó C4) — o sistema ficaria ligado e mudo, inclusive na estatística;
- os críticos C1–C3 são defeitos que **só se manifestam com o sistema funcionando em escala real** (truncagem aos 1000, starvation aos 200 confirmados, fila do L2 bloqueada) — ligar antes de corrigir é plantar corrupção silenciosa no KPI soberano;
- sem freio de créditos (A10), o tier gratuito morre em horas e o pós-estouro é cegueira sem alerta (A7);
- os jogos capturados começam em **15/08** — não há CLV possível antes disso de qualquer forma. Há ~3 semanas de janela natural para corrigir e decidir sem perder amostra nenhuma.

**Sobre os tipsters (E1.4/D4):** a ausência de canais de Telegram **não bloqueia o MVP**. O gatilho soberano do modo sombra é `value_bet` sobre a referência de-vigada — funciona só com odds. Tips são o 4º gatilho + o ranking de tipsters: agregam depois, e o parser ainda tem lacunas (M21) que contaminariam o ranking desde o 1º dia. Prioridade correta: fonte BR (PC-VENUE/D3), que é o caminho crítico para o sistema um dia emitir cartão executável — os tipsters entram quando houver pipeline medindo.

**Roteiro recomendado (ordem):**
1. **Tier 0 de código** (sem doutrina): C1 (paginação), C2 (recorte terminal da fila do L3), C3 (transação do crivo), A5 (`tools`), A6 (calendário stale), A7a (vigia olhar `sports_ok`), A10 (freio de créditos).
2. **Rito**: PC-CALIBRACAO-SEM-VENUE (§7) + semear `mercados_homologados` (SQL acima) + migration dos gates (A1) e do `fn_finalizar_evento_clv` (A11).
3. **Ligar L0** na máquina do Daniel/VPS com cadência adaptativa + freio; cumprir o aceite de 48h do E1; medir créditos (decide D1).
4. **Ligar L1 + L4**: candidatos_sombra e near-miss começam a acumular CLV de calibração na 1ª rodada de agosto. L3 ligado só para alertas administrativos (não haverá cartão — correto).
5. **Em paralelo**: sonda OddsPapi (conta free + `ODDSPAPI_API_KEY`) → decisão PC-VENUE → allowlist D3 → aí nascem os sinais homologáveis.
6. **Depois**: E1.4 (tipsters, com D4) e E6.2 (replay do backtest no VPS).
7. **Correções Tier 2** (M1–M22) intercaladas conforme prioridade: M1 (sincronia intra-referência) e M9/M13 antes de operação prolongada; M2 só importa quando houver venue exchange.

**"Outras áreas ou continuar o MVP?"** Continuar o MVP. As "respostas que faltam" não são novas áreas — são: (a) as decisões de rito acima, (b) a fonte de odds BR, (c) tempo de forno: deixar a calibração acumular as células de amostra ≥200 que a homologação e o E7 exigem. O desenho está íntegro; o que falta é destravar a medição e dar tempo ao KPI.

---

## 9. Lacunas de teste mapeadas (para o backlog)

- `comum/gates.py`: **zero testes** (tripwire pétreo, TTL, `_mais_frouxo`, gate sumido).
- Exposição via orquestrador, `_exposto_do_evento`, dois sinais no mesmo ciclo.
- Reemissão pós-veto; dessincronia entre seleções da referência.
- L2: exceção no insert/transição; campo extra nested; dois JSONs; CONFIRMA incoerente; fronteira 1e-6; `ModeloAnthropic` com cliente mockado (pegaria A5).
- L3: `parse_mode` ausente pinado por teste (é a proteção anti-injeção do cartão); reclaim; flood de supressão; paginação de confirmados; alerta pós-entrega.
- L4: `assentamento_s` exercitado; falha da finalização pós-desfechos.
- L0: AH/OU sem point; campos vazios; `commence_time` alterado; `_loop_adaptativo` inteiro; exceções não-`OddsAPIError`; vigia "pulsando mas 100% falha"; dedup de alertas.

---

*Auditoria conduzida em 25/07/2026 sobre o commit `e72179e` e o banco vivo. Nenhuma correção foi aplicada neste ciclo; nenhuma linha foi semeada — as decisões de §7/§8 são do rito.*
