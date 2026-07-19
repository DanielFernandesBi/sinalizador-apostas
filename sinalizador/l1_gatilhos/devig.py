"""De-vig da referência sharp — método de Shin (Doutrina P1 / §3).

Recupera as probabilidades justas a partir das odds da referência (Pinnacle),
removendo a margem (vig) sob o modelo de insider trading de Shin: uma proporção
`z` do volume vem de insiders, e o método distribui a margem de forma assimétrica
(corrige o favourite-longshot bias — Doutrina P3).

Fórmula e casos de teste ancorados na implementação de referência do método:
mberk/shin — https://github.com/mberk/shin (impl. Python de Shin's method).

Modelo (por seleção i, com π_i = 1/odd_i e booksum B = Σ π_i):

    π_i² / B = (1 − z) · p_i²  +  z · p_i

que, invertido, dá

    p_i = ( sqrt(z² + 4(1−z)·π_i²/B) − z ) / ( 2(1−z) ).

`z` é a menor raiz em [0,1) da equação de normalização Σ_i p_i = 1, i.e.

    Σ_i sqrt(z² + 4(1−z)·π_i²/B) = 2 + (n−2)·z,

resolvida por bisseção (robusta para n = 2 e n ≥ 3; sem caso especial).

Propriedades (ver tests/test_devig_shin.py):
  - as probabilidades resultantes somam 1;
  - book justo (booksum = 1) ⇒ z = 0 e o método coincide com o de-vig
    multiplicativo simples (p_i = π_i / B);
  - reproduz exemplos numéricos publicados (mberk/shin).
"""
from __future__ import annotations

import math


def devig_multiplicativo(odds: list[float]) -> list[float]:
    """De-vig multiplicativo simples: p_i = (1/odd_i) / Σ(1/odd_j)."""
    inv = [1.0 / o for o in odds]
    b = sum(inv)
    return [i / b for i in inv]


def devig_shin(
    odds: list[float], *, tol: float = 1e-12, max_iter: int = 1000
) -> tuple[list[float], float]:
    """Probabilidades justas pelo método de Shin. Retorna (probabilidades, z).

    `odds` são decimais (> 1.0) da referência. Levanta ValueError para entrada
    inválida (menos de 2 seleções, odd ≤ 1, booksum < 1 = arbitragem).
    """
    if len(odds) < 2:
        raise ValueError("Shin exige ao menos 2 seleções")
    if any(o <= 1.0 for o in odds):
        raise ValueError("odds decimais devem ser > 1.0")

    inv = [1.0 / o for o in odds]
    b = sum(inv)
    if b < 1.0:
        raise ValueError(f"booksum {b} < 1: arbitragem, fora do modelo de Shin")
    n = len(odds)
    c = [i * i / b for i in inv]  # π_i² / B

    def f(z: float) -> float:
        # Σ sqrt(z² + 4(1−z)c_i) − (2 + (n−2)z); raiz em (0,1) é o z de Shin.
        soma = sum(math.sqrt(z * z + 4.0 * (1.0 - z) * ci) for ci in c)
        return soma - (2.0 + (n - 2) * z)

    lo, hi = 0.0, 1.0 - 1e-9
    if f(lo) <= tol:
        # book justo (booksum ≈ 1): sem margem, sem insiders → z = 0
        z = 0.0
    else:
        if f(hi) > 0:  # não deveria ocorrer com book válido; falha alto (P6)
            raise ValueError("não foi possível bracketar z em (0,1)")
        for _ in range(max_iter):
            mid = 0.5 * (lo + hi)
            fm = f(mid)
            if abs(fm) < tol:
                lo = hi = mid
                break
            if fm > 0:
                lo = mid
            else:
                hi = mid
        z = 0.5 * (lo + hi)

    if z >= 1.0:
        raise ValueError("z degenerou para 1")

    probs = [
        (math.sqrt(z * z + 4.0 * (1.0 - z) * ci) - z) / (2.0 * (1.0 - z))
        for ci in c
    ]
    return probs, z
