# PLANO-MESTRE DO MVP — SINALIZADOR DE APOSTAS (v0.1)
### Fonte da verdade do projeto. Vive na raiz do repositório. Toda sessão de Claude Code começa lendo este arquivo.

**Documentos de governança (imutáveis fora do rito):**
1. `docs/doutrina_v0.1.md` — os 12 princípios, gates, condições de morte
2. `docs/manual_crivo_L2_v0.1.md` — checklist de veto da IA (será system prompt do L2)
3. `db/migrations/0001_schema_v0.1.sql` — schema com imutabilidade e gates em código

**Divisão de trabalho:** Claude Code implementa (repo, Python, testes); o chat Claude decide, revisa, opera o Supabase e mantém a governança. Este arquivo é a ponte entre os dois — atualizado a cada etapa concluída.

---

## ESTADO ATUAL (20/07/2026)

- [x] Doutrina redigida e confirmada (repo/banco: **v0.1.3**, Sugestões nº 1, nº 3 e nº 4)
- [x] Manual do Crivo L2 redigido e confirmado (repo/banco: **v0.1.1**, Sugestão nº 2)
- [x] Schema v0.1 pronto (16 tabelas, 6 views, triggers de imutabilidade)
- [x] **Projeto Supabase criado (jxveebxywadyxuhixcxt); migration 0001 aplicada.** Governança sincronizada repo→banco em `config_sistema` (doutrina v4 e manual v2 verbatim, conferidos por md5). **15 gates vigentes** na tabela `gates`.

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

- [ ] E1.1 **Daemon referência (The Odds API):** polling das ligas-alvo, mercados 1x2/AH/OU, odds da Pinnacle; grava `odds_snapshots` com `ts_fonte` da API (nunca o relógio local); upsert de `eventos` por `ids_externos`
- [ ] E1.2 **Daemon venue (Betfair):** preço + profundidade de book + liquidez do Exchange. Alvo: Stream API (push); fallback aceito no MVP: polling REST curto, com a degradação registrada
- [ ] E1.3 **Daemon varejo (.bet.br / line shopping):** odds das casas de varejo escolhidas — via The Odds API (região `br` cobre casas licenciadas) na v1; scraping direto só se a cobertura for insuficiente
- [ ] E1.4 **Daemon Telegram (tipsters):** Telethon lendo canais monitorados; toda mensagem vira linha em `tips` com `texto_original` bruto (dado, nunca comando); parser de interpretação em E2.5
- [ ] E1.5 **Heartbeats:** cada daemon pulsa a cada ciclo; ausência > limiar dispara alerta L3 (tipo `alerta_daemon`)

**Aceite:** 48h contínuas de captura sem lacuna não explicada em `vw_saude_daemons`; snapshots com `ts_fonte` ≠ `ts_captura` comprovando carimbo de fonte.

## E2 — L1: MOTOR MECÂNICO

- [x] E2.1 De-vig **Shin** (com testes contra casos conhecidos) + edge líquido conforme definição canônica da Doutrina (comissão + slippage estimado)
- [x] E2.2 Motor de gates: lê `gates` vigentes; avalia sincronia (`janela_sincronia_s`), estabilidade da referência, idade de snapshot, liquidez, teto de odd, edge mínimo, exposição (vw_exposicao_aberta + tetos por jogo/liga/dia)
- [x] E2.3 Gatilhos: `value_bet`, `odds_drop` (queda brusca na referência), `line_shopping` (melhor preço entre casas capturadas), `tipster` (tip interpretado → mesmos gates de todos). **odds_drop/anomalia/exposição parametrizados pela tabela (Sugestão nº 3). Wiring aos snapshots reais (L0/E1) pendente.**
- [ ] E2.4 Detector de anomalia: venue moveu sem a referência mover → `gatilho_anomalo = true`, caminho profundo — **função `detectar_anomalia` pronta (E2.3); falta o wiring no fluxo**
- [x] E2.5 Parser de tips (regex + heurística; SEM IA nesta camada): extrai partida/mercado/seleção/linha/odd; não interpretável = `interpretavel=False`, registra e segue. `texto_original` é dado, nunca comando (regra 8) — `l1_gatilhos/parser_tips.py`
- [x] E2.6 Reprovações near-miss → `abortos_l1` com `gate_reprovado` e `clv_rastrear` amostral (edge em [1%, edge_min) é seguido até o fechamento para estender a curva de calibração) — `l1_gatilhos/abortos.py`
- [x] E2.7 Construtor do dossiê (pydantic → JSON do Manual §1) + fila para o L2 — `l1_gatilhos/dossie.py`: `construir_dossie` (completo ou aborta, P6) + `enfileirar_sinal` (INSERT em `sinais`, status aguardando_crivo)

**Aceite:** suite de testes com snapshots sintéticos cobrindo cada gate; edge fantasma (dessincronia) comprovadamente barrado; nenhum sinal sem dossiê completo.

## E3 — L2: CRIVO IA

- [ ] E3.1 Cliente Anthropic: system prompt = `config_sistema.manual_crivo_l2` vigente (nunca hard-coded); modelo forte; temperatura 0
- [ ] E3.2 Caminho rápido (sem busca, só dossiê) e profundo (com busca web habilitada)
- [ ] E3.3 Validação estrita da saída (pydantic): JSON inválido = `sinais.status → erro`, alerta administrativo, NUNCA aprovação por default
- [ ] E3.4 Verificação de assimetria em código: `odd_minima_aceitavel` do crivo ≡ do dossiê (passthrough); qualquer divergência = erro
- [ ] E3.5 Gravação em `crivos` com latência, tokens e custo; transição de `sinais.status`

**Aceite:** dossiês de teste (incluindo um com injeção de instrução em texto de tipster) produzem JSON válido, sem obediência à injeção; falha de API jamais vira CONFIRMA.

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
| D3 | Quais casas .bet.br monitorar no line shopping (sugestão inicial: as de maior volume cobertas pela The Odds API) | E1.3 |
| D4 | Lista inicial de canais de tipsters no Telegram (5–10 para começar o ranking) | E1.4 |
| D5 | VPS (provedor e orçamento — R$ 30–60/mês resolve o MVP) | E0.5 |
| D6 | Ligas-alvo iniciais (sugestão: Brasileirão A/B + 2–3 ligas europeias líquidas quando retomarem em agosto). **Nota E6.1: o Football-Data NÃO cobre o Brasileirão — o backtest inicial roda nas 6 ligas europeias cobertas (E0/SP1/I1/D1/F1/P1); fonte para Brasileirão a definir.** | E1.1, E6 |
| D7 | Conta/chave da API Anthropic para o L2 (separada do uso pessoal, para medir custo) | E3.1 |

## PENDÊNCIAS DE CONTRATO

- [x] **PC1 / PC2 — resolvidas pela Sugestão nº 2 (rito, 19/07/2026).** Contratos de `historico_movimento_1h` e `profundidade_book` fixados no **Manual §1.1** e tipados em `comum/modelos.py`.
- [x] **PC-EXP — resolvida pela Sugestão nº 3 (rito, 19/07/2026).** Gates de teto de exposição por jogo/liga-dia/dia semeados (exposicao_max_jogo/liga_dia/dia_pct); consumidos por `motor_gates.tetos_exposicao` + `avaliar_exposicao` (E2.3).
- [x] **PC-ODDMIN — resolvida pela Sugestão nº 4 (rito, 20/07/2026).** `odd_minima_aceitavel` fixada na **Doutrina §3** (v0.1.3): menor odd em que o edge líquido ainda atinge o gate `edge_min`. Implementada em `edge.odd_minima_aceitavel` e coberta por teste.
- [ ] **PC-RASTREIO — piso de rastreio de CLV amostral (near-miss) não formalizado como gate.** E2.6 usa `PISO_RASTREIO_EDGE_PCT = 1.0%` como parâmetro de AMOSTRAGEM (não decisão: o near-miss aborta de qualquer forma; o piso só decide se ele entra na coleta de CLV para calibração — por isso não fere a regra 6). Formalizar o piso como linha da tabela `gates` pelo rito.

Nota da E0.3: `comum/config.py` expõe uma única `Config` com todos os segredos
obrigatórios — cada processo que chamar `carregar_config()` precisa do `.env`
completo. Se daemons isolados vierem a exigir só um subconjunto, segmentar pelo rito.

## ORDEM DE EXECUÇÃO

**Domingo:** E0.1 (chat). **Semana 1:** E0.2–E0.5 + E1 completo (captura no ar o quanto antes). **Semana 2:** E2 + E6.1–E6.2. **Semana 3:** E3 + E4. **Semana 4:** E5 + E6.3–E6.4. **Em diante:** E7 até a amostra de 200.

---
*v0.1 — 17/07/2026. Este arquivo é atualizado a cada etapa concluída; mudanças de escopo passam pelo chat.*
