"""Edge líquido — definição canônica da Doutrina §3.

    edge_liquido = p_justa · (odd_venue − 1) · (1 − comissao)
                   − (1 − p_justa)
                   − slippage_estimado

Regra 6 / Doutrina P4: `comissao` NUNCA é constante no código — vem da tabela
`casas` (`comissao_pct` / 100) e é passada pelo chamador. O slippage é estimado
pela liquidez do mercado (P4), também insumo — nunca embutido. `p_justa` é a
probabilidade de-vigada da referência (ver devig.py), nunca uma opinião.
"""
from __future__ import annotations

from typing import Any


def comissao_fracao(casa: dict[str, Any]) -> float:
    """Comissão como fração (0..1) a partir de uma linha da tabela `casas`.

    A fonte é sempre a tabela (`comissao_pct`), nunca uma constante de código.
    """
    pct = casa["comissao_pct"]
    fracao = float(pct) / 100.0
    if not 0.0 <= fracao < 1.0:
        raise ValueError(f"comissao_pct fora de faixa: {pct}")
    return fracao


def edge_liquido(
    p_justa: float,
    odd_venue: float,
    comissao: float,
    slippage: float = 0.0,
) -> float:
    """Edge líquido canônico (Doutrina §3). `comissao` e `slippage` são frações.

    `comissao` deve vir de `comissao_fracao(casa)` — jamais de constante (regra 6).
    """
    if not 0.0 <= p_justa <= 1.0:
        raise ValueError(f"p_justa fora de [0,1]: {p_justa}")
    if odd_venue <= 1.0:
        raise ValueError(f"odd_venue deve ser > 1.0: {odd_venue}")
    if not 0.0 <= comissao < 1.0:
        raise ValueError(f"comissao (fração) fora de [0,1): {comissao}")
    if slippage < 0.0:
        raise ValueError(f"slippage não pode ser negativo: {slippage}")

    ganho = p_justa * (odd_venue - 1.0) * (1.0 - comissao)
    perda = 1.0 - p_justa
    return ganho - perda - slippage
