"""E1.1 — Daemon da região `eu`: referência (Pinnacle) + venues, na MESMA resposta.

A referência sharp é a Pinnacle de-vigada por Shin (Doutrina §3). A chamada da
região `eu` já traz, na mesma resposta, a Pinnacle + a exchange (Betfair) + as
casas de varejo — por isso este daemon captura TODAS, classificando cada uma por
`classificar_casa` (Sugestão nº 6): referência, exchange-proxy (6,5%) e varejo
(venue do modo sombra). Custo de crédito zero adicional — antes se descartava.
Grava snapshots com `ts_fonte` da API e pulsa `l0_referencia`. Se a Pinnacle
sumir da `eu`, a cobertura (E1 aceite #1) para o teste (fail-loud).
"""
from __future__ import annotations

from .captura import PerfilCaptura
from .mapeamento import CASA_REFERENCIA, classificar_casa

DAEMON = "l0_referencia"
REGIAO = "eu"

PERFIL = PerfilCaptura(
    daemon=DAEMON,
    regiao=REGIAO,
    classificar=classificar_casa,   # todas as casas da eu, cada uma classificada
)
