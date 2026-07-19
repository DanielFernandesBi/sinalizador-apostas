"""Testes do de-vig de Shin (E2.1, aceite a/b).

Exemplos numéricos publicados: mberk/shin — https://github.com/mberk/shin
(implementação Python de referência do método de Shin).
"""
import math

import pytest

from sinalizador.l1_gatilhos.devig import devig_multiplicativo, devig_shin


def _soma(xs):
    return math.fsum(xs)


# ---- aceite (a): soma = 1 e degeneração multiplicativa em z = 0 ----

@pytest.mark.parametrize("odds", [
    [2.0, 2.0],
    [1.90, 2.00],
    [2.6, 2.4, 4.3],
    [1.5, 4.5, 7.0],
    [1.30, 5.5, 11.0],
])
def test_probabilidades_somam_um(odds):
    probs, _z = devig_shin(odds)
    assert _soma(probs) == pytest.approx(1.0, abs=1e-9)
    assert all(0.0 < p < 1.0 for p in probs)


def test_book_justo_z_zero_degenera_multiplicativo():
    # booksum = 1 (sem vig): z = 0 e Shin coincide com o multiplicativo simples.
    odds = [2.0, 2.0]
    probs, z = devig_shin(odds)
    assert z == pytest.approx(0.0, abs=1e-12)
    assert probs == pytest.approx([0.5, 0.5], abs=1e-12)
    assert probs == pytest.approx(devig_multiplicativo(odds), abs=1e-12)


def test_vig_baixo_aproxima_multiplicativo():
    # Com vig pequeno, z é pequeno e Shin fica próximo do multiplicativo.
    odds = [1.90, 2.10]  # booksum ~ 1.0025 (vig baixo)
    inv = [1 / o for o in odds]
    assert sum(inv) > 1.0  # há vig
    probs, z = devig_shin(odds)
    assert 0.0 < z < 0.02
    mult = devig_multiplicativo(odds)
    assert probs == pytest.approx(mult, abs=1e-3)


# ---- aceite (b): exemplos numéricos publicados (mberk/shin) ----

def test_exemplo_publicado_tres_vias():
    # Fonte: README de mberk/shin — https://github.com/mberk/shin
    probs, z = devig_shin([2.6, 2.4, 4.3])
    assert probs == pytest.approx(
        [0.37299406033208965, 0.4047794109200184, 0.2222265287474275], abs=1e-9
    )
    assert z == pytest.approx(0.01694251276407055, abs=1e-9)


def test_exemplo_publicado_duas_vias():
    # Fonte: README de mberk/shin — https://github.com/mberk/shin
    probs, z = devig_shin([1.5, 2.74])
    assert probs == pytest.approx(
        [0.6508515815085157, 0.3491484184914841], abs=1e-9
    )
    assert z == pytest.approx(0.03172728540646625, abs=1e-9)


# ---- propriedade: corrige o favourite-longshot bias (Doutrina P3) ----

def test_shin_puxa_do_azarao_para_o_favorito():
    # Shin dá MENOS probabilidade ao azarão (odd alta) que o multiplicativo,
    # e mais ao favorito — a assinatura da correção do favourite-longshot bias.
    odds = [1.5, 4.5, 7.0]
    probs, z = devig_shin(odds)
    mult = devig_multiplicativo(odds)
    assert z > 0
    favorito, azarao = 0, 2
    assert probs[favorito] > mult[favorito]
    assert probs[azarao] < mult[azarao]


# ---- validação de entrada (P6: falha alto) ----

@pytest.mark.parametrize("odds", [[2.0], [], [1.0, 2.0], [0.9, 3.0]])
def test_entrada_invalida_levanta(odds):
    with pytest.raises(ValueError):
        devig_shin(odds)
