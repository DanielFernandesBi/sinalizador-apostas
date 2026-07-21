"""L4 — fechamento e CLV (o KPI soberano, Doutrina P8).

A linha de fechamento é o ÚLTIMO snapshot da referência (Pinnacle, de-vigada por
Shin) antes do início do jogo — não custa chamada nova, já está capturado. O CLV
mede se a odd da EMISSÃO bateu a linha justa de fechamento; é medido para os
sinais (real) e para vetados/abortos (contrafactual — o audit do auditor).

Núcleo (`clv`) é testável com fakes; `execucao`/`relatorio` são wrappers finos;
`cli` amarra o `Banco`.
"""
