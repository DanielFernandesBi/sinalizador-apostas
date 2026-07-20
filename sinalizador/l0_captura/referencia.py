"""E1.1 — Daemon de referência: linha da Pinnacle na região `eu`.

A referência sharp é a Pinnacle de-vigada por Shin (Doutrina §3). Este daemon só
captura o bookmaker `pinnacle` (região `eu`), grava snapshots com `ts_fonte` da
API e pulsa `l0_referencia`. É o pressuposto do desenho referência × line
shopping — se a Pinnacle sumir da `eu`, a cobertura (E1 aceite #1) para o teste.
"""
from __future__ import annotations

from .captura import PerfilCaptura

CASA_REFERENCIA = "pinnacle"
DAEMON = "l0_referencia"
REGIAO = "eu"

PERFIL = PerfilCaptura(
    daemon=DAEMON,
    regiao=REGIAO,
    tipo_casa="referencia",
    aceitar_casa=lambda chave: chave == CASA_REFERENCIA,
)
