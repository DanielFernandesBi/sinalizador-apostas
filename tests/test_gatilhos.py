"""Testes dos gatilhos do L1 (E2.3): odds_drop, anomalia, line_shopping,
exposição em camadas. Limiares vindos da tabela (GatesFake com o seed vigente)."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from sinalizador.l1_gatilhos.gatilhos import (
    detectar_anomalia,
    detectar_odds_drop,
    melhor_preco,
    variacao_pct,
)
from sinalizador.l1_gatilhos.motor_gates import avaliar_exposicao, tetos_exposicao

UTC = timezone.utc
T0 = datetime(2026, 7, 20, 20, 0, 0, tzinfo=UTC)

SEED = {  # idêntico ao banco real (15 gates, Sugestão nº 3)
    "edge_min_pct": "2.0", "odd_teto": "3.30", "liquidez_multiplo_stake": "10",
    "snapshot_idade_max_s": "600", "janela_sincronia_s": "60",
    "exposicao_max_jogo_pct": "3.0", "exposicao_max_liga_dia_pct": "6.0",
    "exposicao_max_dia_pct": "10.0", "drop_min_pct": "3.0", "janela_drop_s": "900",
    "anomalia_move_pct": "3.0", "stake_max_pct": "2.0", "kelly_fracao": "0.25",
    "drawdown_suspensao_pct": "20", "amostra_minima": "200",
}


class GatesFake:
    def __init__(self, valores=None):
        self._v = dict(valores or SEED)

    def get(self, nome):
        return Decimal(self._v[nome])


# ---- variacao_pct ----

def test_variacao_pct_queda():
    serie = [(T0 - timedelta(seconds=600), 2.10), (T0, 2.00)]
    assert variacao_pct(serie, 900, T0) == pytest.approx((2.00 - 2.10) / 2.10 * 100)


def test_variacao_pct_menos_de_dois_pontos_e_zero():
    assert variacao_pct([(T0, 2.0)], 900, T0) == 0.0
    assert variacao_pct([], 900, T0) == 0.0


def test_variacao_pct_ignora_fora_da_janela():
    serie = [(T0 - timedelta(seconds=5000), 3.0), (T0 - timedelta(seconds=100), 2.10), (T0, 2.00)]
    # ponto de 5000s atrás fica fora da janela de 900s
    assert variacao_pct(serie, 900, T0) == pytest.approx((2.00 - 2.10) / 2.10 * 100)


# ---- odds_drop ----

def test_odds_drop_dispara_acima_do_limiar():
    serie = [(T0 - timedelta(seconds=600), 2.10), (T0 - timedelta(seconds=300), 2.05), (T0, 2.00)]
    disparou, queda = detectar_odds_drop(serie, GatesFake(), T0)
    assert disparou is True
    assert queda == pytest.approx((2.10 - 2.00) / 2.10 * 100)  # ~4.76% ≥ 3%


def test_odds_drop_nao_dispara_abaixo_do_limiar():
    serie = [(T0 - timedelta(seconds=300), 2.05), (T0, 2.00)]  # queda ~2.44% < 3%
    disparou, queda = detectar_odds_drop(serie, GatesFake(), T0)
    assert disparou is False
    assert queda < 3.0


def test_odds_drop_subida_nao_dispara():
    serie = [(T0 - timedelta(seconds=300), 2.00), (T0, 2.10)]  # odd subiu → não é drop
    disparou, queda = detectar_odds_drop(serie, GatesFake(), T0)
    assert disparou is False
    assert queda < 0


# ---- anomalia (gatilho_anomalo) ----

def test_anomalia_venue_move_referencia_parada():
    assert detectar_anomalia(move_ref_pct=0.5, move_venue_pct=4.0, gates=GatesFake()) is True


def test_anomalia_nao_quando_referencia_tambem_move():
    assert detectar_anomalia(move_ref_pct=4.0, move_venue_pct=4.0, gates=GatesFake()) is False


def test_anomalia_nao_quando_venue_move_pouco():
    assert detectar_anomalia(move_ref_pct=0.5, move_venue_pct=2.0, gates=GatesFake()) is False


# ---- line_shopping ----

def test_melhor_preco_escolhe_maior_odd():
    venues = [{"casa": "a", "odd": 2.05}, {"casa": "b", "odd": 2.15}, {"casa": "c", "odd": 2.10}]
    assert melhor_preco(venues)["casa"] == "b"


def test_melhor_preco_ignora_invalidos_e_vazio():
    assert melhor_preco([{"casa": "a", "odd": 1.0}, {"casa": "b", "odd": None}]) is None
    assert melhor_preco([]) is None


# ---- exposição em camadas (tetos da tabela × banca) ----

def test_tetos_exposicao_da_tabela():
    tetos = tetos_exposicao(GatesFake(), banca=1000.0)
    assert tetos == {"jogo": 30.0, "liga_dia": 60.0, "dia": 100.0}  # 3% / 6% / 10%


def test_exposicao_reprova_camada_jogo():
    tetos = tetos_exposicao(GatesFake(), banca=1000.0)
    # já exposto 20 no jogo (teto 30); stake 15 → 35 > 30 → reprova
    r = avaliar_exposicao(15.0, exposto={"jogo": 20.0}, tetos=tetos)
    assert not r.aprovado and r.gate_reprovado == "exposicao_jogo"


def test_exposicao_aprova_dentro_de_todas_as_camadas():
    tetos = tetos_exposicao(GatesFake(), banca=1000.0)
    r = avaliar_exposicao(10.0, exposto={"jogo": 10.0, "liga_dia": 30.0, "dia": 50.0}, tetos=tetos)
    assert r.aprovado
