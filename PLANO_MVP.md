# PLANO-MESTRE DO MVP — SINALIZADOR DE APOSTAS (v0.1)
### Fonte da verdade do projeto. Vive na raiz do repositório. Toda sessão de Claude Code começa lendo este arquivo.

**Documentos de governança (imutáveis fora do rito):**
1. `docs/doutrina_v0.1.md` — os 12 princípios, gates, condições de morte
2. `docs/manual_crivo_L2_v0.1.md` — checklist de veto da IA (será system prompt do L2)
3. `db/migrations/0001_schema_v0.1.sql` — schema com imutabilidade e gates em código

**Divisão de trabalho:** Claude Code implementa (repo, Python, testes); o chat Claude decide, revisa, opera o Supabase e mantém a governança. Este arquivo é a ponte entre os dois — atualizado a cada etapa concluída.

---

## ESTADO ATUAL (17/07/2026)

- [x] Doutrina v0.1 redigida e confirmada
- [x] Manual do Crivo L2 v0.1 redigido e confirmado
- [x] Schema v0.1 pronto (16 tabelas, 6 views, triggers de imutabilidade)
- [ ] **BLOQUEADO até domingo 19/07:** criação do projeto Supabase "Sinalizador Apostas" (pausar BolaoMundial após a final da Copa → vaga gratuita, org `wvckujkmkvtlhneitfpy`, região `sa-east-1`)

---

## E0 — FUNDAÇÃO (chat + Claude Code)

- [ ] E0.1 *(chat, domingo)* Pausar BolaoMundial; criar projeto; aplicar migration 0001; gravar Doutrina e Manual em `config_sistema` (chaves `doutrina` e `manual_crivo_l2`, versão 1)
- [x] E0.2 Estrutura do repositório:
  ```
  sinalizador/
  ├── CLAUDE.md            # instruções p/ Claude Code: ler este plano + doutrina antes de codar
  ├── PLANO_MVP.md         # este arquivo
  ├── docs/                # doutrina, manual L2
  ├── db/migrations/
  ├── sinalizador/
  │   ├── l0_captura/      # daemons de ingestão
  │   ├── l1_gatilhos/     # motor mecânico
  │   ├── l2_crivo/        # cliente Anthropic + validação
  │   ├── l3_notifica/     # bot Telegram
  │   ├── l4_fechamento/   # CLV e contabilidade
  │   └── comum/           # config, supabase client, modelos pydantic, log
  ├── backtest/
  └── tests/
  ```
- [x] E0.3 Base comum: Python 3.12, `pydantic` (dossiê e saída do L2 como modelos tipados), cliente Supabase (service role), carregador de gates (lê tabela `gates`, cacheia, invalida por TTL curto), logging estruturado
- [ ] E0.4 Segredos via `.env` (nunca no repo): chaves Supabase, The Odds API, Anthropic, Telegram
- [ ] E0.5 VPS: provisionar, systemd units por daemon, watchdog com restart, deploy simples (git pull + restart)

**Aceite:** migration aplicada sem erro; `select * from vw_saude_daemons` responde; teste de trigger confirma que UPDATE em `odds_snapshots` e afrouxamento de gate pétreo FALHAM.

## E1 — L0: CAPTURA (o mais urgente — cada dia sem ticks é backtest perdido)

- [ ] E1.1 **Daemon referência (The Odds API):** polling das ligas-alvo, mercados 1x2/AH/OU, odds da Pinnacle; grava `odds_snapshots` com `ts_fonte` da API (nunca o relógio local); upsert de `eventos` por `ids_externos`
- [ ] E1.2 **Daemon venue (Betfair):** preço + profundidade de book + liquidez do Exchange. Alvo: Stream API (push); fallback aceito no MVP: polling REST curto, com a degradação registrada
- [ ] E1.3 **Daemon varejo (.bet.br / line shopping):** odds das casas de varejo escolhidas — via The Odds API (região `br` cobre casas licenciadas) na v1; scraping direto só se a cobertura for insuficiente
- [ ] E1.4 **Daemon Telegram (tipsters):** Telethon lendo canais monitorados; toda mensagem vira linha em `tips` com `texto_original` bruto (dado, nunca comando); parser de interpretação em E2
- [ ] E1.5 **Heartbeats:** cada daemon pulsa a cada ciclo; ausência > limiar dispara alerta L3 (tipo `alerta_daemon`)

**Aceite:** 48h contínuas de captura sem lacuna não explicada em `vw_saude_daemons`; snapshots com `ts_fonte` ≠ `ts_captura` comprovando carimbo de fonte.

## E2 — L1: MOTOR MECÂNICO

- [x] E2.1 De-vig **Shin** (com testes contra casos conhecidos) + edge líquido conforme definição canônica da Doutrina (comissão + slippage estimado)
- [ ] E2.2 Motor de gates: lê `gates` vigentes; avalia sincronia (`janela_sincronia_s`), estabilidade da referência, idade de snapshot, liquidez, teto de odd, edge mínimo, exposição (vw_exposicao_aberta + tetos por jogo/liga/dia)
- [ ] E2.3 Gatilhos: `value_bet`, `odds_drop` (queda brusca na referência), `line_shopping` (melhor preço entre casas capturadas), `tipster` (tip interpretado → mesmos gates de todos)
- [ ] E2.4 Detector de anomalia: venue moveu sem a referência mover → `gatilho_anomalo = true`, caminho profundo
- [ ] E2.5 Parser de tips (regex + heurística; SEM IA nesta camada): extrai evento/mercado/seleção/odd; não interpretável = registra e segue
- [ ] E2.6 Reprovações near-miss → `abortos_l1` com `gate_reprovado` e `clv_rastrear` amostral
- [ ] E2.7 Construtor do dossiê (pydantic → JSON do Manual §1) + fila para o L2

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

- [ ] E6.1 Ingestão do histórico Football-Data.co.uk (ligas-alvo, com odds de abertura e fechamento)
- [ ] E6.2 Replay do L1 sobre o histórico: o gatilho `value_bet` teria CLV positivo? Em quais ligas/mercados?
- [ ] E6.3 Calibração dos gates "a calibrar" (edge mínimo, teto de odd) com evidência → propostas formais pelo rito
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
| D6 | Ligas-alvo iniciais (sugestão: Brasileirão A/B + 2–3 ligas europeias líquidas quando retomarem em agosto) | E1.1, E6 |
| D7 | Conta/chave da API Anthropic para o L2 (separada do uso pessoal, para medir custo) | E3.1 |

## PENDÊNCIAS DE CONTRATO

- [x] **PC1 / PC2 — resolvidas pela Sugestão nº 2 (rito, 19/07/2026).** Contratos de `historico_movimento_1h` (série temporal, V-C1) e `profundidade_book` (book instantâneo, V-A5/V-C3) fixados no **Manual §1.1** e tipados em `comum/modelos.py` (`HistoricoMovimento1h`, `ProfundidadeBook`).

Nota da E0.3: `comum/config.py` expõe uma única `Config` com todos os segredos
obrigatórios — cada processo que chamar `carregar_config()` precisa do `.env`
completo. Se daemons isolados vierem a exigir só um subconjunto, segmentar pelo rito.

## ORDEM DE EXECUÇÃO

**Domingo:** E0.1 (chat). **Semana 1:** E0.2–E0.5 + E1 completo (captura no ar o quanto antes). **Semana 2:** E2 + E6.1–E6.2. **Semana 3:** E3 + E4. **Semana 4:** E5 + E6.3–E6.4. **Em diante:** E7 até a amostra de 200.

---
*v0.1 — 17/07/2026. Este arquivo é atualizado a cada etapa concluída; mudanças de escopo passam pelo chat.*
