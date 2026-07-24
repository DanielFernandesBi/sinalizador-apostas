"""Contrato contábil em Decimal (achado #1 do 2º ciclo de auditoria).

Trava os números do rito de 24/07/2026 para stake R$20 @ odd 2,05 — os mesmos que a
função SQL `fn_liquidar_aposta` (migration 0004) deve produzir.
"""
from decimal import Decimal

import pytest

from sinalizador.comum.dinheiro import (
    ValorInvalidoError,
    centavos,
    dec,
    lucro_liquido,
    payout_bruto,
)

STAKE = Decimal("20")
ODD = Decimal("2.05")

# (resultado, payout bruto creditado, variação líquida da banca)
CASOS = [
    ("green",      Decimal("41.00"), Decimal("21.00")),
    ("red",        Decimal("0.00"),  Decimal("-20.00")),
    ("void",       Decimal("20.00"), Decimal("0.00")),
    ("meio_green", Decimal("30.50"), Decimal("10.50")),
    ("meio_red",   Decimal("10.00"), Decimal("-10.00")),
]


@pytest.mark.parametrize("resultado,bruto,liquido", CASOS)
def test_payout_e_lucro_por_resultado(resultado, bruto, liquido):
    assert payout_bruto(resultado, STAKE, ODD) == bruto
    assert lucro_liquido(resultado, STAKE, ODD) == liquido


@pytest.mark.parametrize("resultado,bruto,liquido", CASOS)
def test_variacao_da_banca_e_debito_mais_credito(resultado, bruto, liquido):
    # O contrato inteiro: debita o stake no registro, credita o BRUTO na liquidação.
    # A soma tem de ser exatamente o lucro líquido — é isto que estava errado (a
    # green de R$20 @ 2,05 movia a banca +R$1 em vez de +R$21).
    assert (-STAKE) + bruto == liquido


def test_green_nao_perde_o_stake():
    # Regressão direta do bug: creditar o LUCRO (21) em vez do BRUTO (41) faria a
    # banca variar +1. O payout tem de devolver o stake junto com o lucro.
    assert payout_bruto("green", 20, "2.05") == Decimal("41.00")
    assert payout_bruto("green", 20, "2.05") - STAKE == Decimal("21.00")


def test_decimal_nao_herda_erro_de_float():
    # Caso real de divergência de UM CENTAVO: stake R$19,90 @ 2,05.
    # float:   19.9 * 2.05 = 40.794999999999995 → arredonda para 40,79
    # Decimal: 19.90 × 2.05 = 40.795 (exato)    → arredonda para 40,80
    assert 19.9 * 2.05 == 40.794999999999995     # o float perde o valor exato
    assert round(19.9 * 2.05, 2) == 40.79        # e um centavo some
    assert payout_bruto("green", "19.90", "2.05") == Decimal("40.80")   # o Decimal, não
    assert dec(2.05) == Decimal("2.05")           # via str, sem erro binário
    assert dec(0.1) + dec(0.2) == Decimal("0.3")  # (0.1+0.2 != 0.3 em float)


def test_arredondamento_comercial_em_centavos():
    assert centavos("0.005") == Decimal("0.01")   # ROUND_HALF_UP, não bankers
    assert centavos("2.344") == Decimal("2.34")
    # odd quebrada: 33.33 * 1.333 = 44.428... → 44.43
    assert payout_bruto("green", "33.33", "1.333") == Decimal("44.43")


@pytest.mark.parametrize("stake", [0, -5, "0.00"])
def test_stake_nao_positivo_e_recusado(stake):
    with pytest.raises(ValorInvalidoError):
        payout_bruto("green", stake, ODD)


@pytest.mark.parametrize("odd", [1, "0.99", -2])
def test_odd_ate_1_e_recusada(odd):
    with pytest.raises(ValorInvalidoError):
        payout_bruto("green", STAKE, odd)


def test_resultado_invalido_e_recusado():
    with pytest.raises(ValorInvalidoError):
        payout_bruto("pendente", STAKE, ODD)      # 'pendente' não é resultado final
    with pytest.raises(ValorInvalidoError):
        payout_bruto("ganhou", STAKE, ODD)
