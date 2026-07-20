"""E1.3 — Daemon de varejo (.bet.br / line shopping): casas da região `br`.

Captura TODAS as casas que a região `br` devolver (line shopping — o melhor preço
entre as casas de varejo é o gatilho `line_shopping` do L1). Cada casa nova é
registrada em `casas` como `varejo` na primeira vez que aparece. Pulsa `l0_varejo`.

Quais casas a `br` de fato devolve é o que a cobertura (E1 aceite #1) reporta na
primeira execução — não se presume aqui; captura-se o que vier e registra-se.
"""
from __future__ import annotations

from .captura import PerfilCaptura

DAEMON = "l0_varejo"
REGIAO = "br"

PERFIL = PerfilCaptura(
    daemon=DAEMON,
    regiao=REGIAO,
    tipo_casa="varejo",
    aceitar_casa=lambda _chave: True,  # toda casa de varejo entra no line shopping
)
