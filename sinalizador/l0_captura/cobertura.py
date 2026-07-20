"""E1 aceite #1 — verificação de cobertura (região `eu`), fail-loud só na referência.

A The Odds API **não tem região `br`** (fato conhecido) — casas de varejo .bet.br
não vêm por aqui; sua avaliação é a sonda OddsPapi (PC-VENUE). Então a cobertura
inspeciona a `eu` e reporta, por jogo, os bookmakers disponíveis, com destaque
para a **Pinnacle** (referência sharp) e para **betfair_ex_*** (a Betfair Exchange
aparece na The Odds API sob essas chaves — relevante ao venue, E1.2/PC-VENUE).

Fail-loud PRESERVADO, mas SÓ para o pressuposto da referência: **Pinnacle ausente
na `eu` = erro** (o alicerce do sistema não existe). Ausência de casas de varejo
NÃO é erro aqui — é esperado (não há região `br`).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .the_odds_api import ClienteOddsAPI

_log = logging.getLogger(__name__)

CASA_REFERENCIA = "pinnacle"
PREFIXO_EXCHANGE = "betfair_ex"


class CoberturaInsuficienteError(RuntimeError):
    """O pressuposto da referência (Pinnacle na `eu`) não se sustenta."""


@dataclass(frozen=True)
class JogoCobertura:
    partida: str
    bookmakers: list[str]


@dataclass(frozen=True)
class Cobertura:
    sport: str
    jogos: list[JogoCobertura] = field(default_factory=list)
    casas_eu: set[str] = field(default_factory=set)
    creditos_restantes: int | None = None
    custo_creditos: int = 0

    @property
    def pinnacle_presente(self) -> bool:
        return CASA_REFERENCIA in self.casas_eu

    @property
    def exchanges(self) -> list[str]:
        return sorted(c for c in self.casas_eu if c.startswith(PREFIXO_EXCHANGE))

    def relatorio(self) -> str:
        linhas = [
            f"Cobertura região eu ({self.sport}) — {len(self.jogos)} jogo(s):",
            f"  Pinnacle (referência): {'SIM' if self.pinnacle_presente else 'NÃO'}",
            f"  Betfair Exchange (betfair_ex_*): {self.exchanges or '(nenhuma)'}",
            f"  casas na eu: {sorted(self.casas_eu) or '(nenhuma)'}",
            f"  créditos restantes: {self.creditos_restantes} (custo: {self.custo_creditos})",
            "",
            "  Por jogo:",
        ]
        for j in self.jogos:
            destaque = [b for b in j.bookmakers
                        if b == CASA_REFERENCIA or b.startswith(PREFIXO_EXCHANGE)]
            marca = f"  [ref/exch: {destaque}]" if destaque else ""
            linhas.append(f"    - {j.partida}: {j.bookmakers}{marca}")
        linhas.append("")
        linhas.append("  Nota: a The Odds API não tem região `br` — casas .bet.br são "
                      "avaliadas pela sonda OddsPapi (PC-VENUE), não aqui.")
        return "\n".join(linhas)


def inspecionar(cliente: ClienteOddsAPI, *, sport: str, market: str = "h2h") -> Cobertura:
    """Uma chamada na região `eu` (custo mínimo). Reporta bookmakers por jogo."""
    eu = cliente.buscar_odds(sport, regions="eu", markets=market)
    jogos: list[JogoCobertura] = []
    casas: set[str] = set()
    for ev in eu.eventos:
        bks = sorted(bk["key"] for bk in ev.get("bookmakers", []) if bk.get("key"))
        casas.update(bks)
        partida = f"{ev.get('home_team', '?')} x {ev.get('away_team', '?')}"
        jogos.append(JogoCobertura(partida=partida, bookmakers=bks))
    return Cobertura(
        sport=sport, jogos=jogos, casas_eu=casas,
        creditos_restantes=eu.requests_remaining, custo_creditos=eu.custo_ultima or 0,
    )


def verificar_ou_parar(cob: Cobertura) -> None:
    """Levanta CoberturaInsuficienteError SÓ se a Pinnacle faltar na `eu`."""
    if not cob.pinnacle_presente:
        raise CoberturaInsuficienteError(
            "cobertura insuficiente — PARAR e reportar (não adaptar): Pinnacle "
            f"ausente na região eu (veio: {sorted(cob.casas_eu) or 'nada'}) — a "
            "referência sharp é o alicerce do sistema (Doutrina §3)"
        )
    _log.info("cobertura OK", extra={"sport": cob.sport, "pinnacle_eu": True,
                                     "exchanges": cob.exchanges})
