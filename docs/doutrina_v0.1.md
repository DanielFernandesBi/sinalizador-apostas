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
- **Venue sombra (modo sombra — Sugestão nº 6):** enquanto não houver venue de exchange capturável (sem API da Betfair), o **modo sombra** (Seção 6, paper trading) opera sobre o **melhor preço de varejo (.bet.br)** como venue, com o sinal marcado `sombra_varejo`. Justificativa: o KPI do modo sombra é o **CLV, que não depende de book**; o gate de liquidez (conceito de exchange) é **inaplicável ao varejo**; e em **odd fixa o `slippage = 0` é definição, não otimismo** — o preço exibido é o executável, e a **odd mínima aceitável** é a trava contra movimento até a execução. A honestidade é preservada pela marca `sombra_varejo` no dossiê. **Nada disso libera dinheiro real**, que permanece condicionado ao gate do paper trading (Seção 6). O estimador de slippage volta ao rito quando existir venue de exchange com book capturável.
- **Edge líquido:** `p_justa × (odd_venue − 1) × (1 − comissão) − (1 − p_justa)`, deduzido slippage estimado (no venue sombra de odd fixa, `slippage = 0` por definição — ver acima).
- **Odd mínima aceitável:** menor odd do venue em que o edge líquido ainda atinge o gate `edge_min` — a mesma fronteira que aprova o sinal. Abaixo dela, o sinal está sem valor suficiente e expira na re-checagem de preço do L3.
- **CLV de um sinal:** diferença entre a odd capturada na emissão e a odd de fechamento da referência sharp para o mesmo mercado/seleção, convertidas a probabilidade.
- **Linha de fechamento (Sugestão nº 9):** a **revisão completa mais recente** da referência sharp com carimbo de fonte **anterior ou igual ao início** da partida, desde que sua defasagem em relação ao início não exceda o gate `fechamento_idade_max_s`. A unidade é **indivisível**: `(casa_id, mercado, linha canônica, ts_fonte)`. Uma revisão só serve se contiver **todas** as seleções canônicas do mercado; revisão incompleta é descartada inteira, jamais completada com preços de outra revisão. É **proibido** montar o fechamento tomando o último preço individual de cada seleção — isso produz um book que nunca existiu e contamina o KPI soberano. Quando não houver revisão completa admissível (inexistente, ou completa porém defasada além do gate), **não há CLV** para aquele mercado, e o motivo é **registrado explicitamente** — a amostra nunca se perde em silêncio, porque a ausência de fechamento tende a se concentrar em mercados voláteis ou suspensos perto do início, e some enviesada.
- **Unidade (u):** 1% da banca corrente no momento do sinal.
- **Janela de validade do dado:** idade máxima do snapshot de odds para que o cálculo seja admissível. Provisório: **10 minutos** (a calibrar).
- **Desfecho de CLV (Sugestão nº 10):** todo item auditável — sinal, aborto rastreado ou candidato de calibração — termina **exatamente uma vez**, com desfecho `calculado` ou com uma **indisponibilidade nomeada** (sem referência, sem revisão completa, revisão defasada). A unidade de conclusão é o ITEM, não o evento: um evento com book para um mercado e sem book para outro **não** pode ser dado por concluído — isso apagaria a amostra do segundo. Cada desfecho carrega uma **categoria** (`real`, `contrafactual_l2`, `contrafactual_l1`, `contrafactual_operacional`, `calibracao`) e essas categorias **nunca** se somam na mesma média. Falha de infraestrutura não é desfecho: é pendência a retentar.
- **Unidade estatística (Sugestão nº 11):** a unidade de amostra é a **aposta lógica** — `evento + mercado + linha canônica + seleção + venue` —, não a linha de banco. Uma chave de candidato produz **no máximo um registro durante toda a vida do evento**, qualquer que seja o desfecho (confirmado, vetado, expirado, erro, timeout, aborto ou candidato de calibração). Sem isso, o mesmo candidato voltaria ao crivo a cada ciclo até ser confirmado por variação estocástica do modelo, e o mesmo desfecho esportivo entraria várias vezes na série como se fossem apostas independentes — esvaziando o gate **pétreo** `amostra_minima`, que é justamente o que autoriza qualquer conclusão. Reentrada exigiria um conceito formal de **nova oportunidade** (revisão de origem, preço materialmente novo, período de carência e regra que impeça contar o mesmo resultado esportivo como amostras independentes); enquanto esse conceito não existir pelo rito, **não há reentrada**.
- **Nada nasce após o apito (Sugestão nº 11):** nenhum sinal, aborto ou candidato de calibração pode ser criado para uma partida cujo início já passou. Uma revisão pré-jogo continua dentro da janela de validade do dado por minutos após o apito; sem essa trava, o sistema criaria itens para uma aposta que não existe mais — e, se o fechamento do evento já tiver sido finalizado, o item nunca receberia CLV, deixando a apuração eternamente incompleta. A trava é do BANCO, com a linha do evento travada na mesma transação da criação: verificação em código não sobrevive à concorrência.
- **Estabilidade é afirmação, não ausência de prova (Sugestão nº 11):** "referência estável" só pode ser afirmada com **histórico que o demonstre** — ao menos duas revisões distintas da referência na janela. Sem isso o estado é **indeterminado** e o candidato aborta. Ausência de dado jamais confirma condição positiva (P6). A unidade de medida do movimento é a **revisão distinta**, não o registro: o mesmo estado de mercado recapturado N vezes é uma revisão só.
- **CLV real exige entrega efetiva (Sugestão nº 12):** só conta como **CLV real** a oportunidade cujo **cartão foi entregue ao operador ANTES do início da partida**. Sinal confirmado pelo L2 que o L3 suprimiu (preço fechou ou envelheceu), que ficou preso na fila de envio, ou que chegou depois do apito, **nunca existiu como oportunidade** — e medi-lo como real responderia "que odds o L1 encontrou?" quando a pergunta é "que oportunidades o operador de fato recebeu e podia executar?". As categorias são separadas e **nunca somadas**: `real_entregue` (o KPI que homologa mercado e autoriza dinheiro real), `confirmado_suprimido` (perda por MERCADO — o preço fugiu) e `confirmado_nao_entregue` (perda OPERACIONAL — falha do próprio pipeline). Distinguir as duas últimas é o que permite saber se o sistema está perdendo valor por causa do mercado ou por causa de si mesmo. Fica reservada a categoria `execucao` para o CLV da odd efetivamente apostada, quando houver execução real a comparar.
- **Exposição aberta inclui posição de papel (Sugestão nº 13):** os tetos de exposição (por jogo, por liga/dia, por dia) medem **compromisso**, não apenas dinheiro desembolsado. Contam, somados conforme o regime: (a) **dinheiro real em risco** — aposta registrada e ainda não liquidada; (b) **posição de papel** — o stake nocional de uma oportunidade **entregue** no regime de papel, aberta no momento da entrega confirmada do cartão (o análogo exato do instante em que o dinheiro sairia da banca) e baixada na liquidação de papel, que ocorre quando o fechamento do evento é finalizado, ou quando uma aposta real a substitui; (c) **sinal em voo** — sinal já emitido, ainda sem desfecho e ainda sem posição, porque entre a emissão e a entrega existe uma janela de ciclos em que o compromisso é real e invisível. Sem (b) e (c), no modo sombra a exposição é **sempre zero** e os três tetos nunca reprovam nada: o sistema medido no paper trading não seria o sistema que operaria com dinheiro, e a diferença estaria justamente na dimensão que quebra banca — concentração —, que o CLV não mede. O nocional é o **gravado na emissão**, nunca recalculado a partir da fração de stake com a banca corrente.
- **A fonte governa os fatos do evento (Sugestão nº 14):** identidade, horário de início, participantes e liga de uma partida são fatos EXTERNOS. O sistema não os inventa nem os congela: cada evento tem **identidade única** pelo id da fonte, e toda alteração afirmada pela fonte é **registrada antes de valer**, com antes e depois. **Remarcação invalida; correção não.** Mudança do horário de início cria um mercado novo: a odd de emissão que precificava a partida das 15h não é comparável à linha de fechamento da partida das 20h, e medir uma contra a outra responde a pergunta errada. Todo item emitido até o instante da revisão sai da amostra com motivo próprio; item emitido depois vale normalmente. O empate conta como invalidado — no instante exato da revisão não se sabe de qual mercado veio a emissão, e ambiguidade aborta (P6). Correção de nome de time ou de liga não invalida nada. **Cancelamento não se infere de ausência:** uma fonte que para de listar um jogo cancelado é indistinguível de uma resposta parcial por falha de rede, e tratar a ausência como confirmação seria o mesmo erro que P6 proíbe — o cancelamento é sempre afirmado explicitamente.
- **O preço que conta é o executável (Sugestão nº 15):** duas decisões que pareciam independentes seguem a mesma regra — vale o que o Daniel de fato receberia, não o número da vitrine. **(a) Line shopping é leilão entre preços ELEGÍVEIS.** Uma casa só disputa o venue do sinal se o preço ainda vale: odd válida, snapshot dentro da janela de validade do dado e sincronizado com a revisão de referência. Escolher a maior odd e só depois checar frescor faz um preço morto vencer e matar o candidato inteiro, sem que a casa fresca logo atrás seja sequer avaliada — não gera falso positivo, gera MUDEZ, e mudez não aparece em nenhuma métrica de erro. As casas inelegíveis permanecem no consenso, marcadas com o motivo: apagá-las apagaria a evidência de que foram vistas e descartadas. **(b) O dimensionamento usa o ganho LÍQUIDO.** Kelly é `f = (p·b − q)/b` com `b` = ganho líquido por unidade apostada; onde há comissão, `b = (odd − 1) × (1 − comissão)`. Usar a odd bruta presume prêmio maior que o recebido e superdimensiona o stake — e o stake é o único lugar onde um erro de fórmula vira perda de banca diretamente. Em odd fixa sem comissão as duas formas coincidem, então o modo sombra não muda; a correção morde exatamente onde há custo de execução.
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
| Piso de edge para rastrear CLV de near-miss (`rastreio_edge_min`) | ≥ 1,0% | a calibrar |
| Defasagem máxima da revisão de fechamento em relação ao início (`fechamento_idade_max_s`) | ≤ 600 s | a calibrar |
| Espera após o início para a persistência assentar antes de declarar indisponibilidade (`fechamento_assentamento_s`) | ≥ 600 s | a calibrar |
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

*v0.1.3 — 20/07/2026. Alteração única (Sugestão nº 4): definição canônica de **odd mínima aceitável** — menor odd em que o edge líquido ainda atinge o gate `edge_min`.*

*v0.1.4 — 20/07/2026. Alteração única (Sugestão nº 5): novo gate `rastreio_edge_min` (≥ 1,0%, a calibrar) na §4 — piso de edge para rastrear o CLV de near-miss (quase-sinais logo abaixo de `edge_min`), estendendo a curva de calibração do modo sombra com dado real.*

*v0.1.12 — 25/07/2026. Alteração única (Sugestão nº 15): **o preço que conta é o executável** na §3. Duas correções da mesma família. O line shopping escolhia a maior odd entre todas as casas capturadas e só depois aplicava os gates de frescor e sincronia à vencedora: uma odd velha maior derrubava o candidato e a casa fresca logo atrás nunca era avaliada — enquanto o preço morto fosse o maior, aquele venue não teria vez em ciclo nenhum. Isso não produz sinal errado, produz silêncio, e silêncio não dispara alarme: passa a existir contador próprio para casas executáveis sem preço elegível. E o dimensionamento por Kelly usava `odd − 1` como ganho por unidade, ignorando a comissão que o edge já descontava — na exchange isso presume prêmio maior que o efetivamente recebido e superdimensiona o stake. Com comissão zero as fórmulas coincidem, então o regime de varejo ratificado pela Sugestão nº 6 não muda de comportamento.*

*v0.1.11 — 25/07/2026. Alteração única (Sugestão nº 14): **a fonte governa os fatos do evento** na §3. O evento era criado uma vez e nunca mais atualizado, e sua identidade não tinha índice único — dois ciclos concorrentes podiam criar duas linhas para a mesma partida, partindo os snapshots entre elas até nenhuma revisão de referência ficar completa. Pior: uma partida adiada mantinha o kickoff antigo para sempre, e `inicio_utc` é a pedra em que se apoiam a trava de apito do L1, a recusa de criação após o apito, a fila do fechamento, a linha de fechamento e a posição de papel. Um horário velho não corrompe um desses controles — corrompe todos ao mesmo tempo, e nenhum tem como perceber, porque é código correto operando sobre um fato errado. A identidade passa a ser única, a atualização é transacional, toda revisão fica registrada com antes e depois, remarcação invalida o que foi emitido antes dela, e cancelamento só existe quando afirmado.*

*v0.1.10 — 25/07/2026. Alteração única (Sugestão nº 13): **exposição aberta passa a incluir posição de papel e sinal em voo** na §3. Os três gates de exposição derivavam exclusivamente de apostas reais pendentes; no modo sombra não há aposta nenhuma, então a exposição era sempre zero e `3% por jogo`, `6% por liga/dia` e `10% por dia` jamais reprovavam um candidato sequer. Dez sinais no mesmo jogo passavam juntos. Como a Seção 6 exige que o paper trading valide o sistema que será usado com dinheiro, um controle de risco que só existe quando há dinheiro nunca chega a ser testado — e o CLV, que é o KPI soberano, é indiferente a concentração. A oportunidade **entregue** passa a abrir posição de papel pelo stake nocional; o sinal emitido e ainda sem posição conta como compromisso em voo; e o que o próprio ciclo de emissão já comprometeu entra na conta antes de existir no banco.*

*v0.1.9 — 25/07/2026. Alteração única (Sugestão nº 12): **CLV real passa a exigir entrega efetiva** na §3. A categoria `real` era atribuída a todo sinal com status `confirmado`, sem verificar se o cartão chegou ao operador — então o KPI soberano media a qualidade da odd entre os sinais que o L2 aprovou, e não a qualidade das oportunidades recebidas e executáveis. Como é esse KPI que homologa mercados (E6.4) e autoriza a passagem do paper trading para dinheiro real (E7), medir oportunidade que nunca existiu para o operador infla a evidência que justifica arriscar dinheiro. `real` é substituída por `real_entregue`, `confirmado_suprimido` (perda de mercado) e `confirmado_nao_entregue` (perda operacional), que jamais se somam; fica reservada `execucao` para o CLV da odd efetivamente apostada.*

*v0.1.8 — 25/07/2026. Alteração única (Sugestão nº 11): três definições na §3 que fecham a integridade da amostra e da janela de emissão. **(a) Unidade estatística** — a amostra conta APOSTAS LÓGICAS (evento+mercado+linha+seleção+venue), com no máximo um registro por candidato em toda a vida do evento, qualquer que seja o desfecho; reentrada só com um conceito formal de nova oportunidade, inexistente no MVP. **(b) Nada nasce após o apito** — nenhum item é criado para partida já iniciada, com a trava no BANCO (linha do evento travada na mesma transação), porque verificação em código não sobrevive à concorrência; item criado após a finalização do fechamento nunca receberia CLV. **(c) Estabilidade é afirmação** — "referência estável" exige ao menos duas revisões distintas na janela; sem isso o estado é indeterminado e o candidato aborta, e a unidade de medida do movimento é a revisão distinta, não o registro.*

*v0.1.7 — 24/07/2026. Alteração única (Sugestão nº 10): **desfecho terminal de CLV por item** na §3 e novo gate `fechamento_assentamento_s` (≥ 600 s, a calibrar) na §4. A unidade de conclusão do fechamento deixa de ser o EVENTO e passa a ser o ITEM (sinal, aborto rastreado, candidato de calibração): cada item termina exatamente uma vez, como `calculado` ou como uma **indisponibilidade nomeada**, e o evento só é dado por concluído quando todos os seus itens têm desfecho. O CLV passa a ser classificado por **categoria** — `real` (confirmado), `contrafactual_l2` (vetado pelo crivo), `contrafactual_l1` (near-miss), `contrafactual_operacional` (expirado, erro, timeout do crivo) e `calibracao` (mercado não homologado) —, jamais somados na mesma média: CLV real, decisório, operacional e de calibração respondem perguntas diferentes. Nenhum sinal pode permanecer `aguardando_crivo` depois do início: vira `timeout_crivo`, e o L2 não confirma sinal de partida já iniciada. **Falha de infraestrutura (rede, banco, erro inesperado) NÃO é desfecho terminal** — o item permanece pendente e é retentado, para que indisponibilidade temporária jamais seja gravada como ausência de dado.*

*v0.1.6 — 24/07/2026. Alteração única (Sugestão nº 9): definição canônica de **linha de fechamento** na §3 e novo gate `fechamento_idade_max_s` (≤ 600 s, a calibrar) na §4. A linha de fechamento é a **revisão completa mais recente** da referência com carimbo anterior ou igual ao início, cuja unidade indivisível é `(casa_id, mercado, linha canônica, ts_fonte)` — é **proibido** montar o fechamento tomando o último preço individual de cada seleção, o que produziria um book que nunca existiu. Revisão posterior ao início é rejeitada; revisão completa mais recente porém defasada além do gate não gera CLV. Ausência de revisão completa e defasagem excessiva geram **motivo explícito registrado**, nunca perda silenciosa de amostra. `clv_log.ts_fechamento` passa a guardar o `ts_fonte` real da revisão utilizada (o início segue em `eventos.inicio_utc`, permitindo medir a defasagem por join).*

*v0.1.5 — 21/07/2026. Alteração única (Sugestão nº 6): definição de **venue sombra** na §3 — o modo sombra opera sobre o melhor preço de varejo (.bet.br) enquanto não há exchange capturável, com `sombra_varejo`, gate de liquidez inaplicável e `slippage = 0` por definição em odd fixa (a odd mínima aceitável é a trava). Resolve PC-VENUE-SOMBRA e PC-SLIPPAGE para o modo sombra; dinheiro real segue travado pelo gate da Seção 6.*
