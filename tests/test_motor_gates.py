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


# ---------------- Kelly sobre o ganho LÍQUIDO (P1.9) ----------------

def test_kelly_sem_comissao_e_identico_a_formula_anterior():
    """Regime ratificado hoje é varejo de odd fixa (comissão 0): a correção NÃO pode
    mudar o comportamento do modo sombra."""
    gates = GatesFake()
    for p, odd in ((0.55, 2.10), (0.40, 3.00), (0.70, 1.60)):
        antigo = (p * odd - 1.0) / (odd - 1.0)
        esperado = min(max(antigo, 0.0) * 0.25, 0.02) if antigo > 0 else 0.0
        assert stake_kelly_fracao(p, odd, gates) == pytest.approx(esperado)


def test_comissao_reduz_o_stake_na_exchange():
    """Com 6,5% de comissão o ganho por unidade é menor, logo Kelly é menor. Usar a
    odd bruta presumiria prêmio maior que o recebido e superdimensionaria."""
    gates = GatesFake({**SEED, "stake_max_pct": "100.0"})   # sem teto: Kelly puro
    bruto = stake_kelly_fracao(0.55, 2.10, gates)
    liquido = stake_kelly_fracao(0.55, 2.10, gates, comissao=0.065)
    assert liquido < bruto
    b = (2.10 - 1.0) * (1 - 0.065)
    assert liquido == pytest.approx(((0.55 * b - 0.45) / b) * 0.25)


def test_comissao_pode_zerar_uma_aposta_que_parecia_ter_valor():
    """O caso que importa: edge positivo no bruto, negativo no líquido. Antes o
    sistema dimensionaria stake para uma aposta que já não vale."""
    gates = GatesFake({**SEED, "stake_max_pct": "100.0"})
    p, odd, com = 0.50, 2.05, 0.065
    assert stake_kelly_fracao(p, odd, gates) > 0.0            # bruto: parece valer
    assert stake_kelly_fracao(p, odd, gates, comissao=com) == 0.0


def test_comissao_invalida_e_recusada():
    with pytest.raises(ValueError):
        stake_kelly_fracao(0.55, 2.10, GatesFake(), comissao=1.0)
