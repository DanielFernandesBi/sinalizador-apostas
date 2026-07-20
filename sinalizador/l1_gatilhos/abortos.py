"""E2.6 — registro de abortos near-miss (P7) + rastreio de CLV amostral.

Todo aborto é registrado (Doutrina P7 — o log de abortos é tão valioso quanto o
de sinais). Os quase-sinais (near-miss no edge) são marcados com `clv_rastrear`
para serem seguidos até o fechamento: é assim que o modo sombra estende a curva
de calibração (E6.3) com dado real perto do gate.
"""
from __future__ import annotations

from typing import Any, Optional, Protocol


class _Inseridor(Protocol):
    def inserir(self, tabela: str, registro: dict[str, Any]) -> dict[str, Any]: ...


class ProvedorGates(Protocol):
    def get(self, nome: str): ...  # retorna Decimal (comum/gates.py)


# Piso de edge (em %) para RASTREAR o CLV de um near-miss.
# O teto do intervalo é o gate `edge_min_pct` (da tabela, 2%); o piso (1%) é um
# parâmetro de AMOSTRAGEM — não um gate de decisão: o near-miss é abortado de
# qualquer forma, isto só decide se ele entra na coleta de CLV para calibração.
# Por isso não fere a regra 6. Formalizar como gate é a pendência PC-RASTREIO.
PISO_RASTREIO_EDGE_PCT = 1.0


def deve_rastrear_clv(
    edge_liquido: float, gates: ProvedorGates, *, piso_pct: float = PISO_RASTREIO_EDGE_PCT
) -> bool:
    """True se o near-miss deve ser seguido até o fechamento (CLV amostral).

    Critério: edge no intervalo [piso_pct, edge_min_pct) — logo abaixo do gate,
    onde a calibração mais precisa de evidência. `edge_liquido` é fração.
    """
    edge_pct = edge_liquido * 100.0
    edge_min = float(gates.get("edge_min_pct"))
    return piso_pct <= edge_pct < edge_min


def registrar_aborto(
    banco: _Inseridor,
    *,
    gatilho: str,
    gate_reprovado: str,
    dossie_parcial: dict[str, Any],
    evento_id: Optional[str] = None,
    clv_rastrear: bool = False,
) -> dict[str, Any]:
    """INSERT append-only em `abortos_l1` (P7). Registra qual gate matou o sinal
    e se o quase-sinal será rastreado por CLV (`clv_rastrear`)."""
    return banco.inserir(
        "abortos_l1",
        {
            "gatilho": gatilho,
            "evento_id": evento_id,
            "gate_reprovado": gate_reprovado,
            "dossie_parcial": dossie_parcial,
            "clv_rastrear": clv_rastrear,
        },
    )
