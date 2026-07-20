"""Gatilhos do L1 (E2.3) — determinísticos e SEM IA (regra 2).

Detecta as OPORTUNIDADES; quem aprova/reprova é o motor de gates
(`motor_gates.avaliar` + `avaliar_exposicao`). Todos os limiares vêm da tabela
`gates` via `CarregadorGates` (regra 6 — nunca constante):

  - value_bet     : edge > 0 vs. referência (ver edge.py); gates decidem.
  - odds_drop     : queda ≥ `drop_min_pct` na referência dentro de `janela_drop_s`.
  - line_shopping : melhor preço entre as casas capturadas.
  - tipster       : tip interpretado (parser em E2.5) → MESMOS gates de todos.
  - gatilho_anomalo: venue moveu ≥ `anomalia_move_pct` com a referência parada.

O `tipster` não tem detector próprio aqui: um tip vira candidato e percorre
exatamente o mesmo pipeline de gates (Doutrina — "tip é descoberta, nunca
autoridade"). A exposição em camadas usa `tetos_exposicao` (motor_gates).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional, Protocol


class ProvedorGates(Protocol):
    def get(self, nome: str): ...  # retorna Decimal (ver comum/gates.py)


PontoSerie = tuple[datetime, float]  # (ts_fonte, odd)


def variacao_pct(serie: list[PontoSerie], janela_s: float, agora: datetime) -> float:
    """Variação percentual SINALIZADA da odd na janela [agora−janela_s, agora].

    (odd_recente − odd_antiga) / odd_antiga × 100. Negativa quando a odd caiu.
    Menos de 2 pontos na janela → 0.0 (sem variação mensurável; não dispara nada).
    """
    inicio = agora - timedelta(seconds=janela_s)
    janela = [(ts, odd) for ts, odd in serie if inicio <= ts <= agora]
    if len(janela) < 2:
        return 0.0
    janela.sort(key=lambda p: p[0])
    odd_antiga = janela[0][1]
    odd_recente = janela[-1][1]
    if odd_antiga <= 0:
        return 0.0
    return (odd_recente - odd_antiga) / odd_antiga * 100.0


def detectar_odds_drop(
    serie_ref: list[PontoSerie], gates: ProvedorGates, agora: datetime
) -> tuple[bool, float]:
    """Queda brusca da referência. Retorna (disparou, queda_pct).

    Limiares da tabela: `janela_drop_s` e `drop_min_pct`. Queda = odd que encurtou
    (preço caiu). Dispara quando queda_pct ≥ drop_min_pct.
    """
    janela = float(gates.get("janela_drop_s"))
    drop_min = float(gates.get("drop_min_pct"))
    queda_pct = -variacao_pct(serie_ref, janela, agora)  # queda é variação negativa
    return (queda_pct >= drop_min, queda_pct)


def detectar_anomalia(
    move_ref_pct: float, move_venue_pct: float, gates: ProvedorGates
) -> bool:
    """`gatilho_anomalo`: venue moveu ≥ `anomalia_move_pct` com a referência PARADA.

    Recebe os movimentos (já calculados, ex.: via `variacao_pct`) de cada série,
    para não inventar uma janela que a Doutrina não define. É anomalia quando o
    venue se moveu ao menos o limiar e a referência não (Manual §4.1 → caminho
    profundo / ônus invertido).
    """
    limiar = float(gates.get("anomalia_move_pct"))
    return abs(move_venue_pct) >= limiar and abs(move_ref_pct) < limiar


def melhor_preco(venues: list[dict]) -> Optional[dict]:
    """Line shopping: a casa com o MAIOR preço entre as capturadas (odd > 1).

    `venues`: lista de dicts com ao menos `casa` e `odd`. Retorna o dict vencedor
    (o de maior odd) ou None se nenhum for válido.
    """
    validos = [v for v in venues if (v.get("odd") or 0) > 1.0]
    if not validos:
        return None
    return max(validos, key=lambda v: v["odd"])
