"""Testes do construtor do dossiê + fila para o L2 (E2.7)."""
import pytest

from sinalizador.l1_gatilhos.dossie import (
    DossieIncompletoError,
    construir_dossie,
    enfileirar_sinal,
)
from sinalizador.l1_gatilhos.edge import edge_liquido, odd_minima_aceitavel


def _dossie_dict(**over):
    d = {
        "sinal_id": "s-1",
        "gatilho": "value_bet",
        "gatilho_anomalo": False,
        "caminho": "rapido",
        "evento": {"liga": "Brasileirao A", "partida": "A x B",
                   "data_hora_utc": "2026-07-20T21:30:00Z", "mercado": "1x2", "selecao": "A"},
        "matematica": {"p_justa_shin": 0.52, "odd_referencia": 1.95, "odd_venue": 2.10,
                       "edge_liquido": 0.031, "stake_kelly_quarto": 0.018,
                       "odd_minima_aceitavel": 2.02, "comissao_aplicada": 0.065},
        "snapshots": {"ts_fonte_referencia": "2026-07-20T20:00:05Z",
                      "ts_fonte_venue": "2026-07-20T20:00:07Z", "janela_sincronia_ok": True,
                      "referencia_estavel_ok": True,
                      "historico_movimento_1h": {"referencia": [], "venue": []}},
        "liquidez": {"disponivel_no_preco": 1200.0, "profundidade_book": None,
                     "gate_liquidez_ok": True},
        "venues_comparados": [],
        "exposicao": {"por_jogo": 0, "por_liga_dia": 0, "por_dia": 0, "gates_exposicao_ok": True},
        "tipster": None,
    }
    d.update(over)
    return d


class BancoFake:
    def __init__(self):
        self.inseridos = []

    def inserir(self, tabela, registro):
        self.inseridos.append((tabela, registro))
        return {"id": "sinal-uuid", **registro}


# ---- construir_dossie ----

def test_constroi_dossie_completo():
    d = construir_dossie(_dossie_dict())
    assert d.gatilho == "value_bet"
    assert d.matematica.odd_minima_aceitavel == 2.02


def test_dossie_incompleto_levanta():
    dados = _dossie_dict()
    del dados["matematica"]  # falta bloco obrigatório
    with pytest.raises(DossieIncompletoError):
        construir_dossie(dados)


def test_dossie_malformado_levanta():
    with pytest.raises(DossieIncompletoError):
        construir_dossie(_dossie_dict(gatilho="inexistente"))  # fora do domínio


# ---- enfileirar_sinal (fila do L2) ----

def test_enfileira_em_sinais_com_dossie_e_numeros():
    banco = BancoFake()
    dossie = construir_dossie(_dossie_dict())
    ret = enfileirar_sinal(banco, dossie, evento_id="ev-1", casa_venue_id="casa-1")

    assert len(banco.inseridos) == 1
    tabela, reg = banco.inseridos[0]
    assert tabela == "sinais"
    assert reg["evento_id"] == "ev-1" and reg["casa_venue_id"] == "casa-1"
    assert reg["gatilho"] == "value_bet"
    assert reg["mercado"] == "1x2" and reg["selecao"] == "A"
    assert reg["edge_liquido_pct"] == pytest.approx(3.1)   # 0.031 → %
    assert reg["stake_pct"] == pytest.approx(1.8)          # 0.018 → %
    assert reg["odd_minima_aceitavel"] == 2.02
    # dossiê completo vai em jsonb (datetimes serializados)
    assert reg["dossie"]["evento"]["mercado"] == "1x2"
    assert isinstance(reg["dossie"]["snapshots"]["ts_fonte_venue"], str)
    # status NÃO é setado aqui — usa o default do schema (aguardando_crivo)
    assert "status" not in reg
    assert ret["id"] == "sinal-uuid"


# ---- odd_minima_aceitavel (fronteira do gate edge_min) ----

def test_odd_minima_corresponde_ao_edge_minimo():
    p, com, edge_min = 0.55, 0.065, 0.02
    odd_min = odd_minima_aceitavel(p, com, edge_min)
    # no piso, o edge líquido é exatamente o edge mínimo
    assert edge_liquido(p, odd_min, com) == pytest.approx(edge_min)
    # logo acima do piso, edge > mínimo; logo abaixo, edge < mínimo
    assert edge_liquido(p, odd_min + 0.05, com) > edge_min
    assert edge_liquido(p, odd_min - 0.05, com) < edge_min
