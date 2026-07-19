"""Testes da liquidação de Asian Handicap (decomposição em meias-apostas)."""
import pytest

from backtest.ah import liquidar_ah


# ---- linhas inteiras/meias (sem decomposição) ----

def test_meia_linha_mandante_favorito_ganha():
    # mandante -0.5, vence por 1 (1x0): (1-0) + (-0.5) = 0.5 > 0 → +1
    assert liquidar_ah(-0.5, "mandante", 1, 0) == 1.0


def test_meia_linha_mandante_favorito_perde_no_empate():
    # mandante -0.5, empate 1x1: 0 + (-0.5) = -0.5 → -1
    assert liquidar_ah(-0.5, "mandante", 1, 1) == -1.0


def test_linha_inteira_push():
    # mandante 0.0 (nível), empate → push (devolve)
    assert liquidar_ah(0.0, "mandante", 1, 1) == 0.0


def test_visitante_recebe_handicap():
    # mandante -0.5 ⇒ visitante +0.5; empate 1x1: (1-1) + 0.5 = 0.5 > 0 → +1
    assert liquidar_ah(-0.5, "visitante", 1, 1) == 1.0


def test_linha_inteira_perde_por_um():
    # mandante -1.0, vence por 1 (1x0): (1) + (-1) = 0 → push
    assert liquidar_ah(-1.0, "mandante", 1, 0) == 0.0
    # vence por 2 (2x0): (2) + (-1) = 1 → +1
    assert liquidar_ah(-1.0, "mandante", 2, 0) == 1.0


# ---- linhas de quarto (decomposição em duas metades) ----

def test_quarto_menos_025_meia_perda_no_empate():
    # -0.25 = metade em 0.0 (push) + metade em -0.5 (perde) → (0 + -1)/2 = -0.5
    assert liquidar_ah(-0.25, "mandante", 1, 1) == -0.5


def test_quarto_menos_025_ganha_vencendo():
    # -0.25, vence por 1: 0.0 → +1 ; -0.5 → +1 ; média +1
    assert liquidar_ah(-0.25, "mandante", 1, 0) == 1.0


def test_quarto_menos_075_meio_ganho_vencendo_por_um():
    # -0.75 = metade em -0.5 (+1) + metade em -1.0 (push) → (1 + 0)/2 = 0.5
    assert liquidar_ah(-0.75, "mandante", 1, 0) == 0.5


def test_quarto_mais_025_visitante():
    # mandante +0.25 ⇒ metades 0.0 e +0.5 para o mandante; visitante espelha.
    # jogo 0x1 (visitante vence por 1), lado visitante, linha mandante +0.25:
    # visitante handicap -0.25 → metades -0.0 e -0.5 do visitante:
    #   0.0: (1-0)+0 = 1 → +1 ; -0.5: (1)+(-0.5)=0.5 → +1 ; média +1
    assert liquidar_ah(0.25, "visitante", 0, 1) == 1.0


def test_lado_invalido_levanta():
    with pytest.raises(ValueError):
        liquidar_ah(-0.5, "empate", 1, 0)


@pytest.mark.parametrize("linha,quarto", [
    (-0.25, True), (0.25, True), (-0.75, True), (0.75, True),
    (0.0, False), (-0.5, False), (-1.0, False), (0.5, False), (-1.5, False),
])
def test_deteccao_de_quarto(linha, quarto):
    from backtest.ah import _linha_de_quarto
    assert _linha_de_quarto(linha) is quarto
