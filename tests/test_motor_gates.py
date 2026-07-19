"""Testes do motor de gates (E2.2). Gates do seed vigente (Doutrina §4)."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from sinalizador.l1_gatilhos.motor_gates import (
    ContextoAvaliacao,
    avaliar,
    avaliar_exposicao,
    stake_kelly_fracao,
)

UTC = timezone.utc
T0 = datetime(2026, 7, 20, 20, 0, 0, tzinfo=UTC)

# Seed vigente (idêntico ao banco real jxveebxywadyxuhixcxt).
SEED = {
    "edge_min_pct": "2.0", "odd_teto": "3.30", "liquidez_multiplo_stake": "10",
    "snapshot_idade_max_s": "600", "janela_sincronia_s": "60",
    "stake_max_pct": "2.0", "kelly_fracao": "0.25",
    "drawdown_suspensao_pct": "20", "amostra_minima": "200",
}


class GatesFake:
    def __init__(self, valores=None):
        self._v = dict(valores or SEED)

    def get(self, nome):
        return Decimal(self._v[nome])


def _ctx(**over):
    base = dict(
        odd_venue=2.10,
        edge_liquido=0.05,            # 5% ≥ 2%
        stake_valor=10.0,
        liquidez_disponivel=1000.0,   # ≥ 10×10
        ts_fonte_referencia=T0,
        ts_fonte_venue=T0 + timedelta(seconds=5),  # dentro de 60s
        referencia_estavel_ok=True,
        agora=T0 + timedelta(seconds=30),           # idade 30s < 600
    )
    base.update(over)
    return ContextoAvaliacao(**base)


def test_aprovado_quando_tudo_passa():
    r = avaliar(_ctx(), GatesFake())
    assert r.aprovado and r.gate_reprovado is None


def test_reprova_dessincronia():
    r = avaliar(_ctx(ts_fonte_venue=T0 + timedelta(seconds=120)), GatesFake())
    assert not r.aprovado and r.gate_reprovado == "janela_sincronia_s"


def test_reprova_referencia_instavel():
    r = avaliar(_ctx(referencia_estavel_ok=False), GatesFake())
    assert r.gate_reprovado == "referencia_estavel"


def test_reprova_snapshot_velho():
    r = avaliar(_ctx(agora=T0 + timedelta(seconds=1200)), GatesFake())
    assert r.gate_reprovado == "snapshot_idade_max_s"


def test_reprova_odd_acima_do_teto():
    r = avaliar(_ctx(odd_venue=3.50), GatesFake())
    assert r.gate_reprovado == "odd_teto"


def test_reprova_edge_abaixo_do_minimo():
    r = avaliar(_ctx(edge_liquido=0.015), GatesFake())  # 1.5% < 2%
    assert r.gate_reprovado == "edge_min_pct"


def test_reprova_liquidez_insuficiente():
    r = avaliar(_ctx(liquidez_disponivel=50.0), GatesFake())  # < 10×10
    assert r.gate_reprovado == "liquidez_multiplo_stake"


def test_ordem_sincronia_antes_de_edge():
    # dessincronia E edge baixo → reprova pela sincronia (avaliada primeiro).
    r = avaliar(_ctx(ts_fonte_venue=T0 + timedelta(seconds=120), edge_liquido=0.0), GatesFake())
    assert r.gate_reprovado == "janela_sincronia_s"


# ---- stake por Kelly fracionário com teto (P5) ----

def test_stake_kelly_quarto():
    # p=0.52, odd=2.0 → kelly pleno = (0.52*2.0-1)/1.0 = 0.04; ¼ = 0.01 (< teto 2%).
    frac = stake_kelly_fracao(0.52, 2.0, GatesFake())
    assert frac == pytest.approx(((0.52 * 2.0 - 1) / 1.0) * 0.25)  # ¼ de Kelly = 0.01
    assert frac < 0.02  # abaixo do teto pétreo


def test_stake_limitado_ao_teto_2pct():
    # edge enorme → Kelly ¼ passaria de 2%, mas o teto pétreo corta.
    frac = stake_kelly_fracao(0.90, 2.50, GatesFake())
    assert frac == pytest.approx(0.02)  # stake_max_pct


def test_stake_zero_sem_edge():
    # odd apenas justa (1/p) → kelly pleno 0 → stake 0.
    assert stake_kelly_fracao(0.5, 2.0, GatesFake()) == 0.0


# ---- exposição (tetos ainda não definidos por rito) ----

def test_exposicao_sem_tetos_nao_reprova():
    r = avaliar_exposicao(10.0, exposto={"jogo": 5.0}, tetos={})
    assert r.aprovado  # sem gate de teto definido, nada a reprovar (PC-EXP)


def test_exposicao_reprova_quando_estoura_teto_jogo():
    r = avaliar_exposicao(10.0, exposto={"jogo": 95.0}, tetos={"jogo": 100.0})
    assert not r.aprovado and r.gate_reprovado == "exposicao_jogo"


def test_exposicao_aprova_dentro_do_teto():
    r = avaliar_exposicao(10.0, exposto={"dia": 50.0}, tetos={"dia": 100.0})
    assert r.aprovado
