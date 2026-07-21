"""L2 — o crivo (veto por IA) sobre os sinais enfileirados pelo L1.

Núcleo testável (`crivo`) depende só do Protocol `ModeloCrivo` — sem SDK nem
rede: valida a saída (pydantic estrito), confere o passthrough da odd mínima e
transiciona o sinal (CONFIRMA→confirmado, ABORTA→vetado). O `cli` amarra o
`ModeloAnthropic` real e roda na máquina do Daniel / VPS.

Invariante inviolável: **falha jamais vira CONFIRMA** (E3.3) — qualquer exceção
no caminho leva o sinal a `erro`, com alerta administrativo.
"""
