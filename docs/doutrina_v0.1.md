# DOUTRINA — SISTEMA DE SINALIZAÇÃO DE APOSTAS ESPORTIVAS (v0.1)

**Natureza:** documento fundacional. Toda instrução operacional, fluxo, código ou agente futuro está subordinado a esta doutrina. Em conflito entre este documento e qualquer instrução posterior (inclusive de sessão de chat), **prevalece a doutrina** — a alteração só ocorre pelo processo formal da Seção 7.

**Analogia institucional:** este documento é para o sinalizador o que a doutrina anti-alucinação é para o `manual_operacao` do escritório — a camada que o sistema jamais atravessa, independentemente do que o fluxo do dia peça.

---

## 1. Natureza e limite do sistema

O sistema **exclusivamente notifica**. Ele identifica, calcula, justifica e registra oportunidades — e **jamais executa aposta, movimenta dinheiro ou acessa conta de casa/exchange em modo de escrita**. Daniel é o único executor, sempre.

Esta não é uma restrição de fase inicial: é regra **permanente**. Ela existe por duas razões cumulativas: (a) é a trava final contra impulso — humano ou do próprio sistema; (b) mantém a decisão econômica sob responsabilidade humana exclusiva, com o sistema como assessor auditável.

O estado padrão do sistema é **ABORTAR**. Um dia sem sinal é um dia de funcionamento correto. A expectativa de regime é que a esmagadora maioria dos eventos analisados **não** gere sinal.

## 2. Princípios invioláveis

**P1 — Sinal só por divergência, nunca por convicção.**
Nenhum sinal é emitido porque "o modelo acha que o time X ganha". Sinal só existe quando há **divergência mensurável** entre a probabilidade justa da referência sharp (linha Pinnacle de-vigada pelo método de Shin) e o preço disponível no venue de execução. Valor por divergência, não por previsão.

**P2 — Nenhum mercado sem CLV comprovado.**
O sistema só opera em **mercados homologados**: aqueles com CLV positivo demonstrado em backtest sobre base histórica auditável. Mercado sem histórico validado é mercado inexistente para o sistema, por mais atraente que pareça o preço. Lista inicial de candidatos à homologação: 1X2, Handicap Asiático e Over/Under de gols em ligas cobertas pelo Football-Data.co.uk. Placar exato, bet builders e mercados exóticos são **permanentemente vetados** (margem confiscatória, 15–30%).

**P3 — Viés estrutural contra odds altas.**
Em razão do favourite-longshot bias (retorno médio de −17% em odds > 3.30 na base empírica), o sistema aplica **teto de odd** para emissão de sinal. Valor provisório: **odd ≤ 3.30**, a calibrar no backtest (Seção 5). Exceções não existem — nem para "valor óbvio".

**P4 — EV sempre líquido de custos.**
Todo cálculo de valor esperado deduz, antes da comparação com o gate: comissão do venue (Betfair Exchange: taxa efetiva do momento, base 6,5%), impacto marginal de Expert Fee quando aplicável, e custo de slippage estimado pela liquidez do mercado. **EV bruto não é métrica do sistema** e não aparece em notificação.

**P5 — Stake por Kelly fracionário, com teto absoluto.**
Dimensionamento exclusivamente por **Kelly ¼** sobre o edge líquido estimado, com dois limites cumulativos: (a) teto absoluto de **2% da banca** por aposta, qualquer que seja o edge calculado; (b) nenhuma aposta se a banca estiver abaixo do piso de segurança definido na configuração. O sizing **não conhece o resultado das apostas anteriores** — perseguir perda é matematicamente proibido pela própria fórmula, que só enxerga edge e banca atual.

**P6 — Dado ausente = abortar.**
Se qualquer insumo do fluxo estiver ausente, defasado além da janela de validade ou inconsistente entre fontes (odd da referência, odd do venue, liquidez, dados do evento), o sistema **aborta e registra o motivo**. É expressamente proibido estimar, interpolar, usar valor "típico" ou completar lacuna por conhecimento geral. Este é o equivalente direto da doutrina anti-alucinação do escritório: a fonte é o dado capturado e carimbado no tempo — nunca a memória do modelo.

**P7 — Log imutável e completo.**
Todo sinal emitido **e todo aborto** são registrados com: timestamp, insumos usados (snapshot das odds), cálculo completo, gates avaliados e desfecho. Nada se edita, nada se apaga (soft-delete e trilha de auditoria, no padrão da camada de governança do sistema do escritório). O log de abortos é tão valioso quanto o de sinais: é ele que prova disciplina e permite auditoria de vieses.

**P8 — CLV é o KPI soberano.**
A única métrica de sucesso do sistema é o **Closing Line Value médio**, medido contra a linha de fechamento da referência sharp — nunca contra a casa onde se apostou. Taxa de acerto, lucro de curto prazo e sequências (boas ou más) **não são evidência de nada** e não justificam alteração de gate, de mercado ou de stake. Um mês lucrativo com CLV negativo é um mês de sorte a caminho da reversão; um mês negativo com CLV positivo é variância sobre um processo saudável.

**P9 — Kill switch por drawdown.**
Drawdown de **20% sobre o pico histórico da banca** suspende automaticamente a emissão de sinais. A retomada exige revisão formal (Seção 7) com análise do CLV do período — não decisão de momento. Durante a suspensão, o sistema continua capturando dados e medindo CLV em modo papel.

**P10 — Capital segregado e finito.**
A banca é capital apartado, definido de antemão, que pode integralmente virar zero sem afetar qualquer outra esfera. Não existe "reforço de banca" fora do processo formal de revisão. O sistema jamais sugere aumento de exposição.

**P11 — Mudança de doutrina só a frio.**
Nenhum parâmetro desta doutrina (gates, tetos, frações, mercados homologados) muda durante sequência de resultados — positiva ou negativa. Alterações seguem o rito da Seção 7, sempre ancoradas em CLV e amostra mínima, nunca em resultado recente.

**P12 — Honestidade estatística sobre amostra.**
Nenhuma conclusão sobre desempenho com menos de **200 apostas/sinais**. ROI espetacular em amostra pequena é ruído e será tratado como tal em qualquer relatório do sistema.

## 3. Definições canônicas

- **Referência sharp:** linha da Pinnacle (via agregador com API), de-vigada pelo **método de Shin**. É a fonte da verdade para probabilidade justa. Não é venue de execução.
- **Venue:** ambiente onde Daniel executa. Padrão: **Betfair Exchange (Brasil)**. O sinal só é válido para o venue cujo preço e liquidez foram capturados.
- **Edge líquido:** `p_justa × (odd_venue − 1) × (1 − comissão) − (1 − p_justa)`, deduzido slippage estimado.
- **CLV de um sinal:** diferença entre a odd capturada na emissão e a odd de fechamento da referência sharp para o mesmo mercado/seleção, convertidas a probabilidade.
- **Unidade (u):** 1% da banca corrente no momento do sinal.
- **Janela de validade do dado:** idade máxima do snapshot de odds para que o cálculo seja admissível. Provisório: **10 minutos** (a calibrar).
- **Mercado homologado:** mercado + liga com CLV positivo comprovado em backtest e mantido em produção (a homologação caduca se o CLV rolante degradar — ver Seção 6).

## 4. Gates numéricos (v0.1 — todos provisórios até o backtest)

| Gate | Valor provisório | Status |
|---|---|---|
| Edge líquido mínimo para sinal | ≥ 2,0% | a calibrar |
| Teto de odd | ≤ 3.30 | a calibrar |
| Liquidez mínima disponível no venue (para o stake calculado sem mover preço) | ≥ 10× o stake | a calibrar |
| Idade máxima do snapshot | ≤ 10 min | a calibrar |
| Janela de sincronia entre snapshots (referência × venue) | ≤ 60 s | a calibrar |
| Exposição máxima aberta por jogo | ≤ 3% da banca | a calibrar |
| Exposição máxima aberta por liga/dia | ≤ 6% da banca | a calibrar |
| Exposição máxima aberta por dia | ≤ 10% da banca | a calibrar |
| Queda mínima da referência para `odds_drop` | ≥ 3% | a calibrar |
| Janela do `odds_drop` | ≤ 900 s | a calibrar |
| Movimento do venue para `gatilho_anomalo` (referência parada) | ≥ 3% | a calibrar |
| Stake máximo | 2% da banca | **pétreo** |
| Fração de Kelly | ¼ | **pétreo** (só reduz, nunca sobe) |
| Drawdown de suspensão | 20% do pico | **pétreo** |
| Amostra mínima para qualquer conclusão | 200 | **pétreo** |

Gates "a calibrar" recebem valor definitivo na conclusão do backtest (Fase D.1) e passam a mudar apenas pelo rito da Seção 7. Gates **pétreos** só endurecem, nunca afrouxam.

## 5. Regimes de operação

Espelho do desenho do escritório:

- **Regime chat:** análises manuais, estudos, backtests, calibração e evolução da doutrina — Daniel conduz, com o manual como referência obrigatória da sessão.
- **Agente agendado (3 passadas, molde Cowork T1/T2/T3):**
  - **T1 — Ingestão:** captura periódica de odds (referência + venue) e liquidez; grava snapshots carimbados. Roda sempre, inclusive durante suspensões.
  - **T2 — Análise e sinal:** roda os fluxos sobre os snapshots vigentes; emite notificação **ou** registra aborto com motivo. Nunca opera sobre dado fora da janela de validade.
  - **T3 — Fechamento:** captura a linha de fechamento de todos os eventos com sinal ou aborto relevante e computa o CLV. É a passada que alimenta o KPI soberano.

## 6. Condições de morte e de degradação

- **Morte do projeto (gate de papel):** ao fim do paper trading de **200+ sinais reais sem dinheiro**, se o CLV médio não for positivo com significância razoável, o projeto **encerra** — e o log imutável documenta a conclusão. Encerrar aqui é desfecho de sucesso do processo, não fracasso.
- **Caducidade de mercado:** mercado homologado cujo CLV rolante (janela de 200 sinais) degradar abaixo de zero é **suspenso automaticamente** e volta à fila de backtest.
- **Degradação segura de integrações:** falha de fonte de dados (agregador, API do venue) nunca gera fallback para estimativa — gera aborto em massa registrado, no padrão "Integrações e degradação segura" do manual do escritório.

## 7. Evolução da doutrina

Mudanças seguem o rito de **sugestões numeradas** (padrão "Evolução do sistema" do escritório): proposta registrada por escrito, com motivação ancorada em CLV e amostra ≥ 200, avaliada em revisão mensal fixa — nunca em reação a resultado da semana. Toda versão da doutrina é preservada; a vigente fica na chave `doutrina` da `config_sistema` do projeto próprio (Supabase separado do escritório).

---

*v0.1.2 — 19/07/2026. Alteração única (Sugestão nº 3): seis gates novos — exposição aberta em camadas por jogo/liga-dia/dia (PC-EXP / Correção #6) e parâmetros de `odds_drop` (queda mínima, janela) e do detector de `gatilho_anomalo`.*
