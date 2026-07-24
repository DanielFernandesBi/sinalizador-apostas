"""Aritmética monetária em Decimal (achado #1 do 2º ciclo de auditoria).

Dinheiro NÃO se calcula em float: `20 * 2.05` em binário dá 40.99999999999999, e o
erro se acumula no ledger lançamento a lançamento até a banca não bater com a soma
das suas próprias linhas. Aqui tudo é `Decimal`, quantizado a CENTAVO com
ROUND_HALF_UP (arredondamento comercial, o que a pessoa espera ao conferir).

CONTRATO CONTÁBIL (formalizado na migration 0004, rito de 24/07/2026):
  registro   → debita o STAKE.
  liquidação → credita o PAYOUT BRUTO, DERIVADO de (resultado, stake, odd).

    resultado    payout bruto                 variação final da banca
    green        stake × odd                  +stake × (odd − 1)
    red          0                            −stake
    void         stake                         0
    meio_green   (stake/2 × odd) + stake/2    +stake × (odd − 1) / 2
    meio_red     stake/2                      −stake/2

O payout é DERIVADO, nunca digitado: o valor manual (`--retorno`) era a origem do
bug — debitava-se o stake e creditava-se só o lucro, e a banca só subia o lucro
menos o stake. Este módulo é o espelho, em Python, do `fn_liquidar_aposta` (SQL): a
autoridade do que é GRAVADO é a função SQL (transacional); este módulo serve à
validação, ao preview na CLI e aos testes que travam as duas pontas no mesmo número.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

CENTAVO = Decimal("0.01")

RESULTADOS_FINAIS = ("green", "red", "void", "meio_green", "meio_red")


class ValorInvalidoError(ValueError):
    """Entrada monetária fora do domínio (stake ≤ 0, odd ≤ 1, resultado inválido)."""


def dec(valor: Any) -> Decimal:
    """Converte para Decimal SEM passar por float (str preserva o valor exato).

    `Decimal(0.1)` carrega o erro do binário; `Decimal(str(0.1))` não. Por isso
    tudo que vem de JSON/CLI/banco entra por aqui.
    """
    if isinstance(valor, Decimal):
        return valor
    if valor is None:
        raise ValorInvalidoError("valor monetário ausente")
    try:
        return Decimal(str(valor))
    except Exception as e:  # InvalidOperation e afins
        raise ValorInvalidoError(f"valor monetário inválido: {valor!r}") from e


def centavos(valor: Any) -> Decimal:
    """Quantiza a 2 casas com ROUND_HALF_UP (o arredondamento comercial)."""
    return dec(valor).quantize(CENTAVO, rounding=ROUND_HALF_UP)


def validar_stake(stake: Any) -> Decimal:
    s = centavos(stake)
    if s <= 0:
        raise ValorInvalidoError(f"stake deve ser > 0 (recebido: {s})")
    return s


def validar_odd(odd: Any) -> Decimal:
    o = dec(odd)
    if o <= 1:
        raise ValorInvalidoError(f"odd deve ser > 1 (recebida: {o})")
    return o


def payout_bruto(resultado: str, stake: Any, odd: Any) -> Decimal:
    """Payout BRUTO creditado à banca na liquidação (espelha `fn_liquidar_aposta`).

    É o valor que ENTRA, não o lucro: no green, `stake × odd` (o stake volta junto).
    """
    if resultado not in RESULTADOS_FINAIS:
        raise ValorInvalidoError(
            f"resultado inválido: {resultado!r} (esperado {'|'.join(RESULTADOS_FINAIS)})"
        )
    s = validar_stake(stake)
    o = validar_odd(odd)
    meio = s / 2
    if resultado == "green":
        bruto = s * o
    elif resultado == "red":
        bruto = Decimal("0")
    elif resultado == "void":
        bruto = s
    elif resultado == "meio_green":
        bruto = (meio * o) + meio
    else:  # meio_red
        bruto = meio
    return centavos(bruto)


def lucro_liquido(resultado: str, stake: Any, odd: Any) -> Decimal:
    """Lucro/prejuízo da aposta = payout bruto − stake. Informação DERIVADA: é a
    variação líquida da banca, já que o stake foi debitado no registro."""
    return centavos(payout_bruto(resultado, stake, odd) - validar_stake(stake))
