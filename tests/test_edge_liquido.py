"""Testes do edge líquido (E2.1, aceite c).

Definição canônica (Doutrina §3):
    edge = p_justa·(odd_venue−1)·(1−comissao) − (1−p_justa) − slippage
com comissão SEMPRE vinda da tabela `casas` (nunca constante).
"""
import pytest

from sinalizador.l1_gatilhos.edge import comissao_fracao, edge_liquido


def test_definicao_canonica_valor_exato():
    # p=0.55, odd=2.10, comissão 6.5%, sem slippage.
    p, odd, com = 0.55, 2.10, 0.065
    esperado = p * (odd - 1.0) * (1.0 - com) - (1.0 - p)
    assert edge_liquido(p, odd, com) == pytest.approx(esperado)
    # confere o número fechado também
    assert edge_liquido(p, odd, com) == pytest.approx(0.55 * 1.10 * 0.935 - 0.45)


def test_comissao_vem_da_tabela_casas_nao_constante():
    # A mesma aposta com casas de comissão diferente dá edges diferentes,
    # provando que a comissão é insumo da tabela, não constante embutida.
    casa_betfair = {"nome": "betfair_exchange", "comissao_pct": 6.5}
    casa_zero = {"nome": "pinnacle", "comissao_pct": 0.0}
    p, odd = 0.55, 2.10

    e_betfair = edge_liquido(p, odd, comissao_fracao(casa_betfair))
    e_zero = edge_liquido(p, odd, comissao_fracao(casa_zero))

    assert comissao_fracao(casa_betfair) == pytest.approx(0.065)
    assert e_zero > e_betfair  # comissão maior corrói o edge
    # e_zero usa exatamente comissão 0 lida da linha:
    assert e_zero == pytest.approx(p * (odd - 1.0) - (1.0 - p))


def test_slippage_e_deduzido():
    p, odd, com = 0.55, 2.10, 0.065
    sem = edge_liquido(p, odd, com)
    com_slip = edge_liquido(p, odd, com, slippage=0.01)
    assert com_slip == pytest.approx(sem - 0.01)


def test_edge_negativo_quando_sem_valor():
    # Odd apenas justa (1/p) com comissão > 0 → edge negativo (custo da comissão).
    p = 0.50
    odd_justa = 1.0 / p  # 2.0
    assert edge_liquido(p, odd_justa, 0.065) < 0


@pytest.mark.parametrize("kwargs", [
    {"p_justa": -0.1, "odd_venue": 2.0, "comissao": 0.0},
    {"p_justa": 1.1, "odd_venue": 2.0, "comissao": 0.0},
    {"p_justa": 0.5, "odd_venue": 1.0, "comissao": 0.0},
    {"p_justa": 0.5, "odd_venue": 2.0, "comissao": 1.0},
    {"p_justa": 0.5, "odd_venue": 2.0, "comissao": 0.0, "slippage": -0.01},
])
def test_entrada_invalida_levanta(kwargs):
    with pytest.raises(ValueError):
        edge_liquido(**kwargs)


def test_comissao_fracao_fora_de_faixa_levanta():
    with pytest.raises(ValueError):
        comissao_fracao({"comissao_pct": 150})
