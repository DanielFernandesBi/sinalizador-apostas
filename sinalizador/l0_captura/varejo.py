"""E1.3 — Daemon de varejo (.bet.br / line shopping): casas da região `br`.

NOTA (Sugestão nº 6): a The Odds API NÃO tem região `br` (fato conhecido) — este
daemon roda em degradação segura (422 → sem snapshot, sem gasto de crédito) até
existir fonte .bet.br própria (avaliação da sonda OddsPapi — PC-VENUE). O varejo
da região `eu` já é capturado pelo daemon `l0_referencia` (mesma resposta), então
o modo sombra não depende deste daemon hoje.

Se/quando a `br` devolver casas, elas são classificadas por `classificar_casa`
(varejo, salvo exchange/referência reconhecidas). Pulsa `l0_varejo`.
"""
from __future__ import annotations

from .captura import PerfilCaptura
from .mapeamento import classificar_casa

DAEMON = "l0_varejo"
REGIAO = "br"

PERFIL = PerfilCaptura(
    daemon=DAEMON,
    regiao=REGIAO,
    classificar=classificar_casa,
)
