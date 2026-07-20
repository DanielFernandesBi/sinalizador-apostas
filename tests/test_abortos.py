"""Testes do registro de abortos near-miss + rastreio de CLV amostral (E2.6).

O piso de rastreio é o gate `rastreio_edge_min_pct` (Sugestão nº 5) — lido da
tabela como qualquer gate (regra 6), nunca constante em código.
"""
from sinalizador.l1_gatilhos.abortos import deve_rastrear_clv, registrar_aborto


class GatesFake:
    def __init__(self, edge_min_pct, rastreio_edge_min_pct=1.0):
        self._m = {
            "edge_min_pct": edge_min_pct,
            "rastreio_edge_min_pct": rastreio_edge_min_pct,
        }

    def get(self, nome):
        return self._m[nome]


class BancoFake:
    def __init__(self):
        self.inseridos = []

    def inserir(self, tabela, registro):
        self.inseridos.append((tabela, registro))
        return {"id": "aborto-uuid", **registro}


# ---- deve_rastrear_clv: intervalo [rastreio_edge_min_pct, edge_min_pct) ----

def test_rastreia_near_miss_dentro_do_intervalo():
    gates = GatesFake(edge_min_pct=2.0)
    assert deve_rastrear_clv(0.015, gates) is True     # 1,5% ∈ [1%, 2%)


def test_nao_rastreia_abaixo_do_piso():
    gates = GatesFake(edge_min_pct=2.0)
    assert deve_rastrear_clv(0.005, gates) is False    # 0,5% < piso 1%


def test_nao_rastreia_no_ou_acima_do_gate():
    gates = GatesFake(edge_min_pct=2.0)
    # no gate já é sinal, não aborto por edge — fora do intervalo de rastreio
    assert deve_rastrear_clv(0.020, gates) is False
    assert deve_rastrear_clv(0.031, gates) is False


def test_piso_e_inclusivo():
    gates = GatesFake(edge_min_pct=2.0)
    assert deve_rastrear_clv(0.010, gates) is True     # exatamente no piso (1%)


def test_piso_vem_da_tabela_nao_de_constante():
    # Mudar o gate no banco muda a fronteira — prova que o piso não é hard-coded.
    gates = GatesFake(edge_min_pct=2.0, rastreio_edge_min_pct=1.5)
    assert deve_rastrear_clv(0.012, gates) is False    # 1,2% < piso 1,5%
    assert deve_rastrear_clv(0.016, gates) is True     # 1,6% ∈ [1,5%, 2%)


# ---- registrar_aborto: append-only em abortos_l1 ----

def test_registra_aborto_append_only():
    banco = BancoFake()
    ret = registrar_aborto(
        banco,
        gatilho="value_bet",
        gate_reprovado="edge_min_pct",
        dossie_parcial={"edge_liquido": 0.015},
        evento_id="ev-1",
        clv_rastrear=True,
    )
    assert len(banco.inseridos) == 1
    tabela, reg = banco.inseridos[0]
    assert tabela == "abortos_l1"
    assert reg["gatilho"] == "value_bet"
    assert reg["gate_reprovado"] == "edge_min_pct"
    assert reg["evento_id"] == "ev-1"
    assert reg["clv_rastrear"] is True
    assert reg["dossie_parcial"] == {"edge_liquido": 0.015}
    assert ret["id"] == "aborto-uuid"


def test_clv_rastrear_default_false():
    banco = BancoFake()
    registrar_aborto(
        banco,
        gatilho="odds_drop",
        gate_reprovado="liquidez_min",
        dossie_parcial={},
    )
    _, reg = banco.inseridos[0]
    assert reg["clv_rastrear"] is False
    assert reg["evento_id"] is None
