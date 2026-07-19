"""Asian Handicap — liquidação por decomposição em meias-apostas.

Convenção Football-Data: `AHh` é o handicap do MANDANTE (ex.: -0.5 = mandante dá
meio gol). O handicap do visitante é o simétrico (-AHh).

Linhas de quarto (…, -0.25, +0.75, …) dividem a aposta em DUAS metades nas duas
linhas vizinhas (meia/inteira) e o resultado é a média das duas — daí "meio
ganho" (+0.5), "push" (0) e "meia perda" (-0.5).

Resultado por unidade apostada (back):
  +1.0 ganha | +0.5 meio ganha | 0.0 push (devolve) | -0.5 meia perde | -1.0 perde

Uso no backtest: informativo (o KPI é o CLV, P8) e reaproveitável na liquidação
real de `apostas` (E5). Não participa da decisão nem da medição de CLV.
"""
from __future__ import annotations


def _linha_de_quarto(linha: float) -> bool:
    """True para .25/.75 (quarto de gol). Robusto a float (0.25 é exato em binário)."""
    return int(round(linha * 4)) % 2 != 0


def _liquida_simples(linha: float, lado: str, gols_mandante: int, gols_visitante: int) -> float:
    """Liquida uma linha inteira/meia (sem decomposição). Retorna +1/0/-1."""
    if lado == "mandante":
        margem, h = gols_mandante - gols_visitante, linha
    elif lado == "visitante":
        margem, h = gols_visitante - gols_mandante, -linha
    else:
        raise ValueError(f"lado inválido: {lado!r} (use 'mandante' ou 'visitante')")
    ajustado = margem + h
    if ajustado > 0:
        return 1.0
    if ajustado < 0:
        return -1.0
    return 0.0  # push (só possível em linha inteira)


def liquidar_ah(linha: float, lado: str, gols_mandante: int, gols_visitante: int) -> float:
    """Resultado da aposta AH em `lado` no handicap `linha` (do mandante).

    Linha de quarto → média das duas metades nas linhas vizinhas.
    """
    if _linha_de_quarto(linha):
        baixa = liquidar_ah(linha - 0.25, lado, gols_mandante, gols_visitante)
        alta = liquidar_ah(linha + 0.25, lado, gols_mandante, gols_visitante)
        return (baixa + alta) / 2.0
    return _liquida_simples(linha, lado, gols_mandante, gols_visitante)
