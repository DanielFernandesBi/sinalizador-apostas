"""E1 aceite #1 — verificação de cobertura, fail-loud.

O desenho referência × line shopping PRESSUPÕE que a região `eu` traz a Pinnacle
e a região `br` traz casas de varejo .bet.br. Isto NÃO é adaptável: se o
pressuposto falhar, o teste PARA e reporta (nunca troca de referência nem
"arruma" o desenho por conta própria).

`inspecionar` gasta o mínimo de créditos: um único sport, um único mercado (h2h),
uma chamada por região → 2 créditos no total. `verificar_ou_parar` levanta
`CoberturaInsuficienteError` com o diagnóstico se o pressuposto não se sustentar.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable

from .the_odds_api import ClienteOddsAPI

_log = logging.getLogger(__name__)

CASA_REFERENCIA = "pinnacle"


class CoberturaInsuficienteError(RuntimeError):
    """O pressuposto do desenho (Pinnacle em eu, casas em br) não se sustenta."""


@dataclass(frozen=True)
class Cobertura:
    sport: str
    casas_eu: set[str] = field(default_factory=set)
    casas_br: set[str] = field(default_factory=set)
    creditos_restantes: int | None = None
    custo_creditos: int = 0

    def relatorio(self) -> str:
        pinn = "SIM" if CASA_REFERENCIA in self.casas_eu else "NÃO"
        return (
            f"Cobertura ({self.sport}):\n"
            f"  eu → Pinnacle presente: {pinn}  | casas: {sorted(self.casas_eu) or '(nenhuma)'}\n"
            f"  br → casas .bet.br: {sorted(self.casas_br) or '(nenhuma)'}\n"
            f"  créditos restantes: {self.creditos_restantes} (custo desta verificação: {self.custo_creditos})"
        )


def _casas_da_resposta(eventos: Iterable[dict]) -> set[str]:
    casas: set[str] = set()
    for ev in eventos:
        for bk in ev.get("bookmakers", []):
            if bk.get("key"):
                casas.add(bk["key"])
    return casas


def inspecionar(cliente: ClienteOddsAPI, *, sport: str, market: str = "h2h") -> Cobertura:
    """Uma chamada em `eu` e uma em `br` para o mesmo sport (custo mínimo)."""
    eu = cliente.buscar_odds(sport, regions="eu", markets=market)
    br = cliente.buscar_odds(sport, regions="br", markets=market)
    custo = (eu.custo_ultima or 0) + (br.custo_ultima or 0)
    return Cobertura(
        sport=sport,
        casas_eu=_casas_da_resposta(eu.eventos),
        casas_br=_casas_da_resposta(br.eventos),
        creditos_restantes=br.requests_remaining,
        custo_creditos=custo,
    )


def verificar_ou_parar(cob: Cobertura) -> None:
    """Levanta CoberturaInsuficienteError se o pressuposto do desenho falhar."""
    problemas: list[str] = []
    if CASA_REFERENCIA not in cob.casas_eu:
        problemas.append(
            f"Pinnacle ausente na região eu (veio: {sorted(cob.casas_eu) or 'nada'}) "
            f"— a referência sharp é o alicerce do sistema (Doutrina §3)"
        )
    if not cob.casas_br:
        problemas.append(
            "região br não devolveu nenhuma casa de varejo — sem line shopping "
            "não há gatilho line_shopping nem venue de execução .bet.br"
        )
    if problemas:
        raise CoberturaInsuficienteError(
            "cobertura insuficiente — PARAR e reportar (não adaptar): "
            + "; ".join(problemas)
        )
    _log.info("cobertura OK", extra={"sport": cob.sport,
                                     "pinnacle_eu": True, "casas_br": sorted(cob.casas_br)})
