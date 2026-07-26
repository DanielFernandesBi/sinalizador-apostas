"""Frescor honesto (P1.8): quem é o ÚLTIMO preço, e o que fazer com carimbo futuro.

Dois defeitos irmãos, ambos sobre a mesma pergunta — "este preço ainda vale?":

  1. **Ordem de recência.** `ultimo_snapshot_venue` ordenava por `ts_captura` (o
     relógio de QUEM GRAVOU). Uma resposta atrasada da API é persistida DEPOIS mas
     carimbada ANTES: pelo relógio de captura ela virava "o último snapshot", e um
     preço mais VELHO passava por corrente. A verdade de mercado é a da FONTE.

  2. **Carimbo no futuro.** `(agora − ts)` dá NEGATIVO e passa em qualquer teto de
     idade. O dado mais suspeito do lote — o único que afirma vir do futuro — era
     justamente o que menos apanhava. `idade_s` devolve `None` nesse caso, e as três
     bocas que perguntam idade (cartão, elegibilidade, motor de gates) fecham.

O que NÃO se testa aqui: a tolerância de 60s não é gate de aposta, é margem de
desencontro de relógio. Ela existe para não transformar deriva de NTP em veto.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sinalizador.comum.db import Banco
from sinalizador.comum.tempo import TOLERANCIA_RELOGIO_S, idade_s, para_datetime
from sinalizador.l1_gatilhos.gatilhos import classificar_elegibilidade, melhor_preco
from sinalizador.l1_gatilhos.motor_gates import ContextoAvaliacao, avaliar
from sinalizador.l3_notifica.cartao import janela_fechou, odd_atual

UTC = timezone.utc
T0 = datetime(2026, 7, 20, 20, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------- idade_s (base)

def test_idade_do_passado_e_positiva():
    assert idade_s(T0, T0 - timedelta(seconds=120)) == 120.0


def test_carimbo_ausente_nao_vira_idade_zero():
    """Sem carimbo não há frescor a afirmar — e 0.0 seria 'fresquíssimo' (P6)."""
    assert idade_s(T0, None) is None
    assert idade_s(None, T0) is None


def test_futuro_dentro_da_tolerancia_e_zerado_nao_negativo():
    """Deriva de relógio não é inconsistência: satura em 0.0, não vira crédito."""
    quase = T0 + timedelta(seconds=TOLERANCIA_RELOGIO_S - 1)
    assert idade_s(T0, quase) == 0.0


def test_futuro_alem_da_tolerancia_e_indeterminado():
    adiante = T0 + timedelta(seconds=TOLERANCIA_RELOGIO_S + 1)
    assert idade_s(T0, adiante) is None


# ------------------------------------------------- 1) ordem de recência (db.py)

class _Resp:
    def __init__(self, data):
        self.data = data


class _Consulta:
    """Simula a semântica de PostgREST: filtros, `order` encadeado e `limit`.

    O `order` aqui ORDENA DE VERDADE (chaves aplicadas na ordem em que foram
    pedidas), porque o defeito era exatamente a ordem das chaves — um fake que só
    registrasse as chamadas provaria apenas que o código diz `ts_fonte`, não que a
    linha certa vence.
    """

    def __init__(self, linhas):
        self._linhas = list(linhas)
        self._ordens: list[tuple[str, bool]] = []
        self._limite = None

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self._linhas = [l for l in self._linhas if l.get(col) == val]
        return self

    def is_(self, col, _null):
        self._linhas = [l for l in self._linhas if l.get(col) is None]
        return self

    def order(self, col, desc=False):
        self._ordens.append((col, desc))
        return self

    def limit(self, n):
        self._limite = n
        return self

    def execute(self):
        linhas = list(self._linhas)
        for col, desc in reversed(self._ordens):   # sort estável: última chave primeiro
            linhas.sort(key=lambda l: l[col], reverse=desc)
        return _Resp(linhas[: self._limite] if self._limite else linhas)


class ClienteFake:
    def __init__(self, linhas):
        self._linhas = linhas

    def table(self, nome):
        assert nome == "odds_snapshots"
        return _Consulta(self._linhas)


def _banco(linhas):
    banco = Banco.__new__(Banco)          # sem tocar em rede/config
    banco._c = ClienteFake(linhas)
    return banco


def _snap(ts_fonte, ts_captura, odd):
    return {"evento_id": "ev-1", "casa_id": "casa-1", "mercado": "1x2",
            "selecao": "mandante", "linha": None,
            "ts_fonte": ts_fonte, "ts_captura": ts_captura, "odd": odd}


def test_resposta_atrasada_nao_vira_o_ultimo_preco():
    """Carimbada às 20:00, gravada às 20:12 — não pode vencer a de 20:10.

    Este é o caso real: a chamada de 20:00 demorou, a de 20:10 respondeu antes.
    Por `ts_captura` a linha VELHA (2.50) seria "a atual" e o cartão sairia com um
    preço que o mercado já abandonou.
    """
    linhas = [
        _snap("2026-07-20T20:00:00+00:00", "2026-07-20T20:12:00+00:00", 2.50),
        _snap("2026-07-20T20:10:00+00:00", "2026-07-20T20:10:05+00:00", 2.05),
    ]
    atual = _banco(linhas).ultimo_snapshot_venue("ev-1", "casa-1", "1x2", "mandante", None)
    assert atual["ts_fonte"] == "2026-07-20T20:10:00+00:00"
    assert float(atual["odd"]) == 2.05


def test_empate_de_fonte_desempata_pela_captura():
    """Mesmo `ts_fonte` (recaptura do mesmo estado): vence a gravação mais recente.

    Não é preferência estética — sem o desempate a linha devolvida seria arbitrária,
    e duas leituras seguidas poderiam divergir sem que nada tivesse mudado.
    """
    linhas = [
        _snap("2026-07-20T20:10:00+00:00", "2026-07-20T20:10:05+00:00", 2.05),
        _snap("2026-07-20T20:10:00+00:00", "2026-07-20T20:11:00+00:00", 2.06),
    ]
    atual = _banco(linhas).ultimo_snapshot_venue("ev-1", "casa-1", "1x2", "mandante", None)
    assert float(atual["odd"]) == 2.06


# --------------------------------------- 2) carimbo no futuro nas três bocas

def test_cartao_nao_usa_preco_carimbado_no_futuro():
    """`odd_atual` → None, e `janela_fechou(None)` suprime o envio (fail-safe)."""
    snap = _snap((T0 + timedelta(hours=2)).isoformat(), T0.isoformat(), 2.20)
    odd = odd_atual(snap, agora=T0, idade_max_s=600.0)
    assert odd is None
    assert janela_fechou(odd, 2.00) is True


def test_venue_com_carimbo_no_futuro_e_inelegivel():
    venues = [
        {"casa": "A", "odd": 2.40, "ts_fonte": T0 + timedelta(hours=2)},   # "fresquíssima"
        {"casa": "B", "odd": 2.10, "ts_fonte": T0 - timedelta(seconds=30)},
    ]
    marcados = classificar_elegibilidade(
        venues, ts_referencia=T0 - timedelta(seconds=20), agora=T0,
        idade_max_s=600.0, janela_sincronia_s=60.0)
    a, b = marcados
    assert a["elegivel"] is False and a["motivo_inelegivel"] == "carimbo_no_futuro"
    assert b["elegivel"] is True
    # E o line shopping, rodando só sobre as elegíveis, escolhe a MENOR odd honesta.
    vencedora = melhor_preco([v for v in marcados if v["elegivel"]])
    assert vencedora["casa"] == "B"


class _GatesFake:
    _V = {"edge_min_pct": "2.0", "odd_teto": "3.30", "liquidez_multiplo_stake": "10",
          "snapshot_idade_max_s": "600", "janela_sincronia_s": "60"}

    def get(self, nome):
        return Decimal(self._V[nome])


def test_gate_reprova_carimbo_no_futuro():
    """A idade negativa passaria em `snapshot_idade_max_s`; o gate próprio não deixa."""
    ctx = ContextoAvaliacao(
        odd_venue=2.10, edge_liquido=0.05, stake_valor=10.0, liquidez_disponivel=1000.0,
        ts_fonte_referencia=T0 + timedelta(hours=2),
        ts_fonte_venue=T0 + timedelta(hours=2, seconds=5),
        referencia_estavel_ok=True, agora=T0)
    r = avaliar(ctx, _GatesFake())
    assert not r.aprovado and r.gate_reprovado == "carimbo_no_futuro"


def test_para_datetime_ilegivel_fecha_o_cartao():
    """Carimbo ilegível → `para_datetime` None → `idade_s` None → sem preço atual."""
    assert para_datetime("nao-e-uma-data") is None
    snap = _snap("nao-e-uma-data", T0.isoformat(), 2.20)
    assert odd_atual(snap, agora=T0, idade_max_s=600.0) is None
