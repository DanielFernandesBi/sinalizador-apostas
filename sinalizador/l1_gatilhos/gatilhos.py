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

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Protocol


class ProvedorGates(Protocol):
    def get(self, nome: str): ...  # retorna Decimal (ver comum/gates.py)


PontoSerie = tuple[datetime, float]  # (ts_fonte, odd)


@dataclass(frozen=True)
class Movimento:
    """Movimento de uma série na janela — e se ele é sequer MENSURÁVEL (P0.4).

    A distinção existe porque `0.0` significava duas coisas opostas: "o preço não
    se moveu" e "não há histórico para dizer se moveu". A segunda virava
    `referencia_estavel = True` — dado ausente confirmando uma condição positiva,
    o oposto de P6. Agora quem chama é obrigado a tratar `mensuravel=False`.

    A unidade é a REVISÃO DISTINTA (ts_fonte distintos), não o ponto: o mesmo
    estado de mercado recapturado três vezes é UMA revisão, e tratá-lo como três
    pontos fabricaria uma medição de movimento que não existe.
    """
    mensuravel: bool
    variacao_pct: Optional[float]
    revisoes: int

    @property
    def pct(self) -> float:
        """Variação em pontos percentuais; 0.0 quando não mensurável.

        SÓ para quem já decidiu o que fazer com `mensuravel` — nunca como atalho
        para ignorar a indeterminação.
        """
        return self.variacao_pct if self.variacao_pct is not None else 0.0


def variacao_pct(serie: list[PontoSerie], janela_s: float, agora: datetime) -> Movimento:
    """Variação percentual SINALIZADA da odd na janela [agora−janela_s, agora].

    (odd_recente − odd_antiga) / odd_antiga × 100. Negativa quando a odd caiu.
    Menos de DUAS REVISÕES distintas na janela → não mensurável (`mensuravel=False`),
    jamais 0.0: ausência de histórico não é ausência de movimento.
    """
    inicio = agora - timedelta(seconds=janela_s)
    janela = [(ts, odd) for ts, odd in serie if inicio <= ts <= agora]
    # Revisões distintas: recaptura do mesmo carimbo não cria histórico novo.
    por_ts = {ts: odd for ts, odd in sorted(janela, key=lambda p: p[0])}
    if len(por_ts) < 2:
        return Movimento(mensuravel=False, variacao_pct=None, revisoes=len(por_ts))
    carimbos = sorted(por_ts)
    odd_antiga = por_ts[carimbos[0]]
    odd_recente = por_ts[carimbos[-1]]
    if odd_antiga <= 0:
        return Movimento(mensuravel=False, variacao_pct=None, revisoes=len(por_ts))
    pct = (odd_recente - odd_antiga) / odd_antiga * 100.0
    return Movimento(mensuravel=True, variacao_pct=pct, revisoes=len(por_ts))


def detectar_odds_drop(
    serie_ref: list[PontoSerie], gates: ProvedorGates, agora: datetime
) -> tuple[bool, float]:
    """Queda brusca da referência. Retorna (disparou, queda_pct).

    Limiares da tabela: `janela_drop_s` e `drop_min_pct`. Queda = odd que encurtou
    (preço caiu). Dispara quando queda_pct ≥ drop_min_pct.
    """
    janela = float(gates.get("janela_drop_s"))
    drop_min = float(gates.get("drop_min_pct"))
    mov = variacao_pct(serie_ref, janela, agora)
    if not mov.mensuravel:
        # Sem histórico não há queda comprovada — não se inventa movimento (P6).
        return (False, 0.0)
    queda_pct = -mov.pct  # queda é variação negativa
    return (queda_pct >= drop_min, queda_pct)


def detectar_anomalia(
    move_ref: Movimento, move_venue: Movimento, gates: ProvedorGates
) -> bool:
    """`gatilho_anomalo`: venue moveu ≥ `anomalia_move_pct` com a referência PARADA.

    Recebe os movimentos (já calculados, via `variacao_pct`) de cada série, para não
    inventar uma janela que a Doutrina não define. É anomalia quando o venue se moveu
    ao menos o limiar e a referência não (Manual §4.1 → caminho profundo / ônus
    invertido).

    Exige que AMBOS sejam mensuráveis: "referência parada" só é afirmável com
    histórico que o demonstre. Sem isso não há anomalia a declarar — a indeterminação
    é tratada antes, pelo gate de estabilidade.
    """
    if not (move_ref.mensuravel and move_venue.mensuravel):
        return False
    limiar = float(gates.get("anomalia_move_pct"))
    return abs(move_venue.pct) >= limiar and abs(move_ref.pct) < limiar


def melhor_preco(venues: list[dict]) -> Optional[dict]:
    """Line shopping: a casa com o MAIOR preço entre as capturadas (odd > 1).

    `venues`: lista de dicts com ao menos `casa` e `odd`. Retorna o dict vencedor
    (o de maior odd) ou None se nenhum for válido.
    """
    validos = [v for v in venues if (v.get("odd") or 0) > 1.0]
    if not validos:
        return None
    return max(validos, key=lambda v: v["odd"])
