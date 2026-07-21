# PLANO-MESTRE DO MVP — SINALIZADOR DE APOSTAS (v0.1)
### Fonte da verdade do projeto. Vive na raiz do repositório. Toda sessão de Claude Code começa lendo este arquivo.

**Documentos de governança (imutáveis fora do rito):**
1. `docs/doutrina_v0.1.md` — os 12 princípios, gates, condições de morte
2. `docs/manual_crivo_L2_v0.1.md` — checklist de veto da IA (será system prompt do L2)
3. `db/migrations/0001_schema_v0.1.sql` — schema com imutabilidade e gates em código

**Divisão de trabalho:** Claude Code implementa (repo, Python, testes); o chat Claude decide, revisa, opera o Supabase e mantém a governança. Este arquivo é a ponte entre os dois — atualizado a cada etapa concluída.

---

## ESTADO ATUAL (21/07/2026)

- [x] Doutrina redigida e confirmada (repo/banco: **v0.1.5**, Sugestões nº 1, nº 3, nº 4, nº 5 e nº 6 — §-sombra)
- [x] Manual do Crivo L2 redigido e confirmado (repo/banco: **v0.1.1**, Sugestão nº 2)
- [x] Schema v0.1 pronto (16 tabelas, 6 views, triggers de imutabilidade)
- [x] **Projeto Supabase criado (jxveebxywadyxuhixcxt); migration 0001 aplicada.** Governança sincronizada repo→banco em `config_sistema` (**doutrina v6** = v0.1.5 e manual v2 verbatim, conferidos por md5). **16 gates vigentes** na tabela `gates`.
- [x] **E3 (crivo L2) implementado** — `l2_crivo/` (modelo Anthropic injetável, crivo com validação estrita + passthrough + fila). 13 testes de crivo.
- [x] **Sugestão nº 6 (executável) + Sugestão nº 7 + higiene de saída** — (a) perfil de captura da `eu` grava TODAS as casas classificadas (Pinnacle referência, `betfair_ex_*` exchange-proxy 6,5%, demais varejo) na mesma resposta (crédito zero adicional); (b) `banca_papel` (config_sistema, R$ 1.000) dimensiona o modo sombra quando o ledger real está vazio, com o dossiê marcando `banca_origem=papel` (ledger real intocado até o E7); (c) logger `httpx`→WARNING + snapshots em lote (1 POST por ciclo). Suíte completa **179 testes verdes**.

---

## E0 — FUNDAÇÃO (chat + Claude Code)

- [x] E0.1 *(chat)* Projeto criado; migration 0001 aplicada; Doutrina e Manual gravados/sincronizados em `config_sistema`
- [x] E0.2 Estrutura do repositório:
  ```
  sinalizador/
  ├── CLAUDE.md            # instruções p/ Claude Code: ler este plano + doutrina antes de codar
  ├── PLANO_MVP.md         # este arquivo
  ├── docs/                # doutrina, manual L2
  ├── db/migrations/
  ├── scripts/             # sync_governanca (repo → config_sistema)
  ├── sinalizador/
  │   ├── l0_captura/      # daemons de ingestão
  │   ├── l1_gatilhos/     # motor mecânico (devig, edge, gates, gatilhos, dossiê)
  │   ├── l2_crivo/        # cliente Anthropic + validação
  │   ├── l3_notifica/     # bot Telegram
  │   ├── l4_fechamento/   # CLV e contabilidade
  │   └── comum/           # config, supabase client, modelos pydantic, log, gates
  ├── backtest/
  └── tests/
  ```
- [x] E0.3 Base comum: Python 3.12, `pydantic` (dossiê e saída do L2 como modelos tipados), cliente Supabase (service role), carregador de gates (lê tabela `gates`, cacheia, invalida por TTL curto), logging estruturado
- [ ] E0.4 Segredos via `.env` (nunca no repo): chaves Supabase, The Odds API, Anthropic, Telegram. **Ao ter o `.env`: rodar `python -m scripts.sync_governanca` (deve reportar "em-dia" — a paridade já foi feita via conector).**
- [ ] E0.5 VPS: provisionar, systemd units por daemon, watchdog com restart, deploy simples (git pull + restart)

**Aceite:** migration aplicada sem erro; `select * from vw_saude_daemons` responde; teste de trigger confirma que UPDATE em `odds_snapshots` e afrouxamento de gate pétreo FALHAM.

## E1 — L0: CAPTURA (o mais urgente — cada dia sem ticks é backtest perdido)

- [x] E1.1 **Daemon da região `eu` (The Odds API):** polling das 6 ligas europeias, mercados 1x2/AH/OU (h2h/spreads/totals). **Sugestão nº 6 (executável):** a mesma resposta traz Pinnacle + exchange + ~20 casas de varejo — captura-se TODAS, classificadas por `mapeamento.classificar_casa` (Pinnacle→referência 0%; `betfair_ex_*`→exchange-proxy 6,5% — SEM book pela API, `liquidez=None`; demais→varejo, venue do modo sombra). Custo de crédito **zero adicional**. `odds_snapshots` gravados em LOTE (1 POST/ciclo) com `ts_fonte` da API; upsert de `eventos` por `ids_externos.odds_api`. `l0_captura/{the_odds_api,mapeamento,persistencia,captura,referencia}.py` + `cli.py`. **Núcleo testável com fakes; a PRIMEIRA execução real + cobertura + medição de créditos rodam na máquina do Daniel (aceite #4) — dependem do `.env` com `the_odds_api_key` (D1).**
- [ ] E1.2 **Daemon venue (Betfair):** preço + profundidade de book + liquidez do Exchange. Alvo: Stream API (push); fallback aceito no MVP: polling REST curto, com a degradação registrada. **SUSPENSO aguardando resposta do developer support (app key).** Enquanto isso, o único venue de execução com liquidez não existe — ver PC-VENUE-SOMBRA.
- [ ] E1.3 **Daemon varejo (.bet.br / line shopping):** **a The Odds API NÃO tem região `br`** (fato conhecido) — o `varejo.py` (região `br`) é, na prática, não-funcional por essa fonte (as chamadas 422 e caem em degradação segura, sem gastar sinal). A FONTE das casas .bet.br está em avaliação: **sonda OddsPapi** (`l0_captura/sonda_oddspapi.py` + `cli.py sonda` — free tier, reporta casas BR licenciadas, Pinnacle, frescor, mercados). É sonda experimental, NÃO integração — adotar (ou não) é decisão do rito (**PC-VENUE**). Daniel cria a conta free e põe `ODDSPAPI_API_KEY` no `.env`.
- [ ] E1.4 **Daemon Telegram (tipsters):** Telethon lendo canais monitorados; toda mensagem vira linha em `tips` com `texto_original` bruto (dado, nunca comando); parser de interpretação em E2.5 (já pronto)
- [x] E1.5 **Heartbeats:** cada ciclo pulsa `banco.pulsar(daemon, detalhe)` com créditos/contagens; o **vigia** (`l0_captura/vigia.py`) lê `vw_saude_daemons` e grava `notificacao` `alerta_daemon` para daemon em silêncio > limiar (entrega Telegram é E4.3). Limiar é parâmetro operacional (não gate).

**Aceite:** 48h contínuas de captura sem lacuna não explicada em `vw_saude_daemons`; snapshots com `ts_fonte` ≠ `ts_captura` comprovando carimbo de fonte. **Aceite adicional do rito (E1):** (1) cobertura da 1ª execução consulta só a região `eu` (não há `br` na The Odds API) e imprime, por jogo, os bookmakers — com destaque para a Pinnacle e para `betfair_ex_*`; fail-loud SÓ no pressuposto da referência (**Pinnacle ausente na `eu` = erro**); ausência de casas de varejo NÃO é erro (`cli.py cobertura`); (2) consumo de créditos por ciclo logado (headers `x-requests-remaining/used/last`) para dimensionar o tier pago (gratuito = 500/mês → cadência baixa); (3) `ts_fonte` da API desde o 1º tick; (4) 1º teste na máquina do Daniel (VPS é E0.5). **Pendências reais:** rodar de fato (rede + `.env`) e reportar cobertura/créditos. (Wiring L0→L1 já feito — E2.8.)

## E2 — L1: MOTOR MECÂNICO

- [x] E2.1 De-vig **Shin** (com testes contra casos conhecidos) + edge líquido conforme definição canônica da Doutrina (comissão + slippage estimado)
- [x] E2.2 Motor de gates: lê `gates` vigentes; avalia sincronia (`janela_sincronia_s`), estabilidade da referência, idade de snapshot, liquidez, teto de odd, edge mínimo, exposição (vw_exposicao_aberta + tetos por jogo/liga/dia)
- [x] E2.3 Gatilhos: `value_bet`, `odds_drop` (queda brusca na referência), `line_shopping` (melhor preço entre casas capturadas), `tipster` (tip interpretado → mesmos gates de todos). **odds_drop/anomalia/exposição parametrizados pela tabela (Sugestão nº 3). Wiring aos snapshots reais (L0/E1) FEITO — ver E2.8.**
- [x] E2.4 Detector de anomalia: venue moveu sem a referência mover → `gatilho_anomalo = true`, caminho profundo — `detectar_anomalia` **plugado no fluxo** (`orquestrador.py`): move do venue vs. move da referência na `janela_drop_s`; `gatilho_anomalo` marca `caminho=profundo`.
- [x] E2.8 **Wiring L0→L1** (`l1_gatilhos/orquestrador.py` + `cli.py`): lê `odds_snapshots`, agrupa por (evento, mercado, linha), de-viga Shin a referência, edge (comissão da tabela `casas`), roda gatilhos + gates + exposição → **sinal** (dossiê completo + fila do L2) **ou** `abortos_l1` (near-miss com `clv_rastrear`). Pulsa heartbeat `l1`. Sem banca real nem de papel → não dimensiona (P5/P6). **Política de venue:** `retail_sombra` (**padrão do modo sombra, ratificado pela Sugestão nº 6** — venue = varejo; ver PC-VENUE-SOMBRA) | `exchange` (doutrina-puro, quando houver exchange com book). Núcleo testável com fakes; roda na máquina do Daniel via `python -m sinalizador.l1_gatilhos.cli --once`.
- [x] E2.5 Parser de tips (regex + heurística; SEM IA nesta camada): extrai partida/mercado/seleção/linha/odd; não interpretável = `interpretavel=False`, registra e segue. `texto_original` é dado, nunca comando (regra 8) — `l1_gatilhos/parser_tips.py`
- [x] E2.6 Reprovações near-miss → `abortos_l1` com `gate_reprovado` e `clv_rastrear` amostral (edge em [1%, edge_min) é seguido até o fechamento para estender a curva de calibração) — `l1_gatilhos/abortos.py`
- [x] E2.7 Construtor do dossiê (pydantic → JSON do Manual §1) + fila para o L2 — `l1_gatilhos/dossie.py`: `construir_dossie` (completo ou aborta, P6) + `enfileirar_sinal` (INSERT em `sinais`, status aguardando_crivo)

**Aceite:** suite de testes com snapshots sintéticos cobrindo cada gate; edge fantasma (dessincronia) comprovadamente barrado; nenhum sinal sem dossiê completo.

## E3 — L2: CRIVO IA

- [x] E3.1 Cliente Anthropic (`l2_crivo/modelo.py`): system prompt = `config_sistema.manual_crivo_l2` vigente (nunca hard-coded; carregado por `crivo.carregar_manual`); modelo forte. **Sem `temperatura 0` — ver PC-CRIVO-TEMP.** Núcleo depende só do Protocol `ModeloCrivo` (testável com fake, sem SDK/rede); SDK amarrado só no `cli.py`.
- [x] E3.2 Caminho **rápido** (sem busca, só dossiê) e **profundo** (ferramenta de busca web habilitada quando `dossie.caminho == "profundo"`) — repasse do caminho verificado em teste.
- [x] E3.3 Validação estrita da saída (`CrivoSaida`, `extra=forbid`): JSON inválido / fora do schema / veredicto fora do domínio / id divergente = `sinais.status → erro` + notificação administrativa, **NUNCA aprovação por default**. Qualquer exceção (rede, SDK) → `erro`, jamais CONFIRMA.
- [x] E3.4 Verificação de assimetria em código (`verificar_passthrough`): `odd_minima_aceitavel` do crivo ≡ do dossiê (tol 1e-6); qualquer divergência = erro.
- [x] E3.5 Gravação em `crivos` (verdict, fatores, latência, tokens, custo_usd auditável) + `transicionar_status_sinal` (CONFIRMA→confirmado, ABORTA→vetado). `processar_fila` pulsa heartbeat `l2`.

**Aceite:** dossiês de teste (incluindo um com injeção de instrução em texto de tipster) produzem JSON válido, sem obediência à injeção; falha de API jamais vira CONFIRMA. **✔ coberto** por `tests/test_crivo.py` (13 testes: CONFIRMA→confirmado, ABORTA→vetado, JSON inválido→erro, schema violado→erro, veredicto fora do domínio→erro, id divergente→erro, passthrough divergente→erro, exceção do modelo→erro, injeção-é-dado-não-comando, injeção→CONFIRMA-malformado→erro, fila/heartbeat).

## E4 — L3: NOTIFICAÇÃO

- [ ] E4.1 Bot Telegram (canal privado do Daniel): cartão do sinal — evento, mercado/seleção, odd, edge líquido, stake sugerido (valor e %), **odd mínima aceitável**, gatilho, veredicto e observação do crivo
- [ ] E4.2 Re-checagem de preço no envio: se odd atual < mínima, sinal `expirado`, sem notificação
- [ ] E4.3 Alertas distintos de sinal: daemon mudo, drawdown, erro L2
- [ ] E4.4 Registro em `notificacoes`

**Aceite:** latência captura→notificação medida; sinal expirado não notifica; alerta de daemon chega quando um daemon é derrubado de propósito.

## E5 — L4: FECHAMENTO E CLV

- [ ] E5.1 Job de fechamento: captura linha de fechamento da referência de todo evento com sinal, veto ou aborto rastreado (agendado por horário de início dos jogos)
- [ ] E5.2 Cômputo de CLV → `clv_log` (real e `contrafactual`); tips fechados → `tips.clv_pct`
- [ ] E5.3 Registro manual de execução: comando no bot ("apostei X a odd Y") → `apostas` + `banca_ledger`
- [ ] E5.4 Liquidação de resultados (resultado do jogo → green/red/void) + `banca_ledger`
- [ ] E5.5 Relatório diário no Telegram: sinais, vetos, CLV do dia, CLV acumulado, banca, drawdown, saúde dos daemons

**Aceite:** `vw_clv_global`, `vw_clv_por_veto` e `vw_tipster_ranking` populando com dados reais.

## E6 — BACKTEST (paralelo, desde que E2.1 exista)

- [x] E6.1 Ingestão do histórico Football-Data.co.uk (ligas-alvo, com odds de abertura e fechamento) — `backtest/football_data.py` (E0/SP1/I1/D1/F1/P1)
- [ ] E6.2 Replay do L1 sobre o histórico: o gatilho `value_bet` teria CLV positivo? Em quais ligas/mercados? — **motor de replay + medição de CLV + relatório prontos e testados (`backtest/replay.py`, 1x2/OU/AH); aguarda execução sobre dados reais (download bloqueado neste ambiente, roda no VPS/dev). Temporadas recomendadas: `--temporadas 2425 2324 2223 2122 2021 1920` (a 24/25 completa é a mais relevante). Recorte da tabela de células = `value_bet_provisional=True`; candidatos completos em `candidatos.csv`.**
- [ ] E6.3 Calibração dos gates "a calibrar" (edge mínimo, teto de odd, etc.) com evidência → propostas formais pelo rito. **Alimentada também pela coleta amostral de CLV dos near-miss (E2.6, `clv_rastrear`).**
- [ ] E6.4 Homologação inicial de mercados → `mercados_homologados`

**Aceite:** relatório de backtest com CLV por liga/mercado e amostra ≥ 200 por célula homologada.

## E7 — MODO SOMBRA (o portão final antes de dinheiro)

- [ ] E7.1 Pipeline completo rodando em produção SEM dinheiro por 200+ sinais
- [ ] E7.2 Auditoria: CLV dos confirmados vs vetados (o crivo agrega ou destrói valor?)
- [ ] E7.3 Decisão formal documentada: CLV ≤ 0 → projeto encerra (Doutrina §6); CLV > 0 → banca inicial e go-live

---

## DECISÕES PENDENTES DO DANIEL (insumos que só você pode dar)

| # | Decisão | Necessária para |
|---|---|---|
| D1 | Plano da The Odds API (tier gratuito = 500 req/mês, insuficiente; tier pago conforme cadência desejada) | E1.1 |
| D2 | Conta Betfair Brasil + solicitação de chave de API (app key) | E1.2 |
| D3 | Quais casas .bet.br monitorar no line shopping — **depende da fonte (PC-VENUE): a The Odds API não cobre `br`; avaliar OddsPapi via `cli.py sonda`** | E1.3 |
| D4 | Lista inicial de canais de tipsters no Telegram (5–10 para começar o ranking) | E1.4 |
| D5 | VPS (provedor e orçamento — R$ 30–60/mês resolve o MVP) | E0.5 |
| D6 | Ligas-alvo iniciais (sugestão: Brasileirão A/B + 2–3 ligas europeias líquidas quando retomarem em agosto). **Nota E6.1: o Football-Data NÃO cobre o Brasileirão — o backtest inicial roda nas 6 ligas europeias cobertas (E0/SP1/I1/D1/F1/P1); fonte para Brasileirão a definir.** | E1.1, E6 |
| D7 | Conta/chave da API Anthropic para o L2 (separada do uso pessoal, para medir custo) | E3.1 |

## PENDÊNCIAS DE CONTRATO

- [x] **PC1 / PC2 — resolvidas pela Sugestão nº 2 (rito, 19/07/2026).** Contratos de `historico_movimento_1h` e `profundidade_book` fixados no **Manual §1.1** e tipados em `comum/modelos.py`.
- [x] **PC-EXP — resolvida pela Sugestão nº 3 (rito, 19/07/2026).** Gates de teto de exposição por jogo/liga-dia/dia semeados (exposicao_max_jogo/liga_dia/dia_pct); consumidos por `motor_gates.tetos_exposicao` + `avaliar_exposicao` (E2.3).
- [x] **PC-ODDMIN — resolvida pela Sugestão nº 4 (rito, 20/07/2026).** `odd_minima_aceitavel` fixada na **Doutrina §3** (v0.1.3): menor odd em que o edge líquido ainda atinge o gate `edge_min`. Implementada em `edge.odd_minima_aceitavel` e coberta por teste.
- [x] **PC-RASTREIO — resolvida pela Sugestão nº 5 (rito, 20/07/2026).** O piso de rastreio de CLV amostral virou o gate `rastreio_edge_min_pct` (= 1,0%, a calibrar), semeado e vigente na tabela `gates` (16 no total) e inscrito na **Doutrina §4** (v0.1.4). `abortos.deve_rastrear_clv` lê o piso da tabela (regra 6) — deixou de ser constante em código.
- [x] **PC-VENUE-SOMBRA — resolvida pela Sugestão nº 6 (rito, 21/07/2026).** O `retail_sombra` foi **ratificado como o venue do modo sombra**: o wiring (E2.8) e o `cli.py` do L1 passam a ter `retail_sombra` como padrão (venue = melhor preço de **varejo**). O sinal nasce `sombra_varejo=true` no dossiê; a proteção de execução é a `odd_minima_aceitavel` contra o preço real no app; o gate de liquidez segue **inaplicável** (varejo não tem book). O modo sombra mede CLV (KPI soberano), que não exige book. **Dinheiro real continua travado pelo gate do E7.** Inscrito na **Doutrina §-sombra** (v0.1.5). `--venue exchange` fica disponível para quando houver exchange cap turável (E1.2 / exchange-proxy).
- [ ] **PC-EXCHANGE-PROXY — ativação do `betfair_ex_*` como venue (aguarda relatório).** A Sugestão nº 6 (executável) faz o L0 **capturar** a exchange-proxy da `eu` (`betfair_ex_*`, tipo exchange, 6,5%, SEM book pela API) — o dado já entra (alimenta CLV/relatório). Mas o venue do modo sombra segue **só varejo**: a proxy fica FORA dos sinais sombra até o rito ratificar seu tratamento sem-book (odd fixa vs. exchange) "com o relatório na mão". Sob `--venue exchange` (doutrina-puro), a proxy sem book **aborta** no gate de liquidez (honesto). Decidir: ativar a proxy como venue paralelo rotulado (com que regra de liquidez) ou aguardar a Betfair (E1.2).
- [ ] **PC-VENUE — fonte das casas de varejo .bet.br (The Odds API não tem região `br`).** Em avaliação pela **sonda OddsPapi** (`l0_captura/sonda_oddspapi.py` + `cli.py sonda`, free tier): reporta casas BR licenciadas presentes, Pinnacle, frescor (timestamps) e mercados. É sonda de avaliação, não integração — **decidir por rito** se a OddsPapi (ou outra fonte) vira a origem do line shopping .bet.br. Nota: a rota/base da OddsPapi no cliente é provisória (confirmar na doc real); o parsing é defensivo. Daniel: conta free + `ODDSPAPI_API_KEY` no `.env`.
- [x] **PC-SLIPPAGE — resolvida para o modo sombra pela Sugestão nº 6 (rito, 21/07/2026).** Em varejo de odd fixa, `slippage=0` **não é otimismo — é definição** (o preço no app é o preço executado; não há book para varrer). O edge do L1 no modo sombra usa `slippage=0` por desenho, inscrito na **Doutrina §-sombra** (v0.1.5). *(Fica reaberta apenas se/quando houver venue de exchange com book (E1.2): aí o estimador por liquidez da P4 volta a ser exigido.)*
- [ ] **PC-CRIVO-TEMP — desvio de `temperatura 0` no L2 (registrado, 21/07/2026).** O PLANO E3.1 pedia `temperatura 0`. O modelo forte usado **rejeita `temperature` (HTTP 400)**; o determinismo é buscado por `output_config.effort` baixo + instrução do Manual, não por `temp 0`. `l2_crivo/modelo.py` **não envia `temperature`**. É um desvio consciente do texto do PLANO — sem impacto no aceite (validação estrita + passthrough + falha-nunca-CONFIRMA independem da temperatura). Ratificar/registrar no rito.

Nota da E0.3 (config por camada — FEITO): `comum/config.py` exige na carga só os
segredos UNIVERSAIS (Supabase). Os demais (`the_odds_api_key`, `anthropic_api_key`,
`telegram_*`, `oddspapi_api_key`) são opcionais no schema e cobrados no PONTO DE USO
por `Config.exigir(campo)` — cada camada falha alto só pelo segredo que ELA consome
(o L0 não precisa da chave do Telegram, etc.).

Nota da E2.8 (L1) + Sugestão nº 7 (banca de papel): o L1 só dimensiona stake se
houver banca. Com o **ledger real vazio**, o L1 agora usa a **banca de papel** —
chave `banca_papel` na `config_sistema` (nominal R$ 1.000, vigente) — e o dossiê
nasce marcado `banca_origem=papel`. O **ledger real (`banca_ledger`) fica
intocado** até o gate do E7. Só quando NÃO há ledger real *nem* `banca_papel` o L1
pulsa `motivo=sem_banca` e não gera sinal (P5/P6). (Para dimensionar sobre uma
banca real de papel via ledger — em vez do nominal — basta semear um `aporte` em
`banca_ledger`; `vw_banca` passa a ter saldo e o L1 usa o real, `banca_origem=real`.)

## ORDEM DE EXECUÇÃO

**Domingo:** E0.1 (chat). **Semana 1:** E0.2–E0.5 + E1 completo (captura no ar o quanto antes). **Semana 2:** E2 + E6.1–E6.2. **Semana 3:** E3 + E4. **Semana 4:** E5 + E6.3–E6.4. **Em diante:** E7 até a amostra de 200.

---
*v0.1 — 17/07/2026. Este arquivo é atualizado a cada etapa concluída; mudanças de escopo passam pelo chat.*
