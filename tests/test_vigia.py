"""Testes do vigia de heartbeats (E1.5): silêncio → notificacao alerta_daemon."""
from sinalizador.l0_captura.vigia import (
    alertar_mudos,
    daemons_mudos,
    rodar_vigia,
)


class BancoFake:
    def __init__(self, saude):
        self._saude = saude
        self.inseridos = []

    def saude_daemons(self):
        return self._saude

    def inserir(self, tabela, registro):
        self.inseridos.append((tabela, registro))
        return {"id": 1, **registro}


def test_daemon_saudavel_nao_alerta():
    banco = BancoFake([
        {"daemon": "l0_referencia", "segundos_em_silencio": 30.0},
        {"daemon": "l0_varejo", "segundos_em_silencio": 45.0},
    ])
    assert daemons_mudos(banco, limiar_s=3600) == []


def test_silencio_alem_do_limiar_e_mudo():
    banco = BancoFake([
        {"daemon": "l0_referencia", "segundos_em_silencio": 5000.0},
        {"daemon": "l0_varejo", "segundos_em_silencio": 30.0},
    ])
    mudos = daemons_mudos(banco, limiar_s=3600)
    assert [m["daemon"] for m in mudos] == ["l0_referencia"]


def test_daemon_que_nunca_pulsou_e_mudo():
    banco = BancoFake([{"daemon": "l0_referencia", "segundos_em_silencio": 10.0}])
    mudos = daemons_mudos(banco, limiar_s=3600, esperados=("l0_referencia", "l0_varejo"))
    varejo = [m for m in mudos if m["daemon"] == "l0_varejo"][0]
    assert varejo["motivo"] == "nunca pulsou" and varejo["segundos"] is None


def test_alertar_grava_notificacao_alerta_daemon():
    banco = BancoFake([])
    inseridas = alertar_mudos(banco, [{"daemon": "l0_varejo", "segundos": None, "motivo": "nunca pulsou"}])
    assert len(inseridas) == 1
    tabela, reg = banco.inseridos[0]
    assert tabela == "notificacoes"
    assert reg["tipo"] == "alerta_daemon" and reg["entregue"] is False
    assert "l0_varejo" in reg["conteudo"]
    assert reg["sinal_id"] is None


def test_rodar_vigia_detecta_e_alerta():
    banco = BancoFake([{"daemon": "l0_referencia", "segundos_em_silencio": 9000.0}])
    mudos = rodar_vigia(banco, limiar_s=3600, esperados=("l0_referencia",))
    assert [m["daemon"] for m in mudos] == ["l0_referencia"]
    assert banco.inseridos[0][1]["tipo"] == "alerta_daemon"
