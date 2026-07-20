"""Testes do ciclo de captura (E1.1/E1.3): snapshots, heartbeat e créditos."""
from _fixtura_odds import evento

from sinalizador.l0_captura import referencia, varejo
from sinalizador.l0_captura.captura import rodar_ciclo
from sinalizador.l0_captura.the_odds_api import OddsAPIError, RespostaOdds


class BancoFake:
    def __init__(self, casas=None):
        self.eventos = {}
        self.casas = casas or {"pinnacle": {"id": "casa-pinn"}}
        self.inseridos = []
        self.pulsos = []
        self._seq = 0

    def evento_por_id_externo(self, fonte, valor):
        return self.eventos.get(valor)

    def casa_por_nome(self, nome):
        return self.casas.get(nome)

    def inserir(self, tabela, registro):
        self._seq += 1
        row = {"id": f"{tabela}-{self._seq}", **registro}
        self.inseridos.append((tabela, row))
        if tabela == "eventos":
            self.eventos[registro["ids_externos"]["odds_api"]] = row
        if tabela == "casas":
            self.casas[registro["nome"]] = row
        return row

    def pulsar(self, daemon, detalhe=None):
        self.pulsos.append((daemon, detalhe))

    def snapshots(self):
        return [r for (t, r) in self.inseridos if t == "odds_snapshots"]


class ClienteFake:
    def __init__(self, por_sport, *, custo=3, restantes=497):
        self.por_sport = por_sport
        self.custo = custo
        self.restantes = restantes
        self.chamadas = []

    def buscar_odds(self, sport, *, regions, markets):
        self.chamadas.append((sport, regions, markets))
        v = self.por_sport.get(sport, [])
        if isinstance(v, Exception):
            raise v
        return RespostaOdds(eventos=v, requests_remaining=self.restantes,
                            requests_used=3, custo_ultima=self.custo,
                            regioes=regions, mercados=markets)


def test_referencia_captura_so_pinnacle_e_pulsa():
    banco = BancoFake()
    cli = ClienteFake({"soccer_epl": [evento(casas=("pinnacle", "bet365_br"))]})
    r = rodar_ciclo(banco, cli, referencia.PERFIL, sports=("soccer_epl",))

    # só pinnacle: h2h(3)+ou(2)+ah(2) = 7 snapshots, 1 evento, 1 casa
    assert r.snapshots == 7 and r.eventos == 1 and r.casas_vistas == 1
    assert {s["casa_id"] for s in banco.snapshots()} == {"casa-pinn"}
    assert cli.chamadas == [("soccer_epl", "eu", "h2h,spreads,totals")]
    # heartbeat do daemon com créditos no detalhe
    assert banco.pulsos[0][0] == "l0_referencia"
    assert banco.pulsos[0][1]["custo_creditos"] == 3
    assert banco.pulsos[0][1]["creditos_restantes"] == 497
    assert r.custo_creditos == 3


def test_varejo_registra_casas_novas():
    banco = BancoFake()  # só pinnacle seedada
    cli = ClienteFake({"soccer_epl": [evento(casas=("bet365_br", "betano_br"))]})
    r = rodar_ciclo(banco, cli, varejo.PERFIL, sports=("soccer_epl",))

    assert r.snapshots == 14 and r.casas_vistas == 2
    casas_criadas = [reg["nome"] for (t, reg) in banco.inseridos if t == "casas"]
    assert set(casas_criadas) == {"bet365_br", "betano_br"}
    assert all(reg["tipo"] == "varejo" for (t, reg) in banco.inseridos if t == "casas")
    assert banco.pulsos[0][0] == "l0_varejo"


def test_evento_nao_duplicado_entre_sports_ou_ciclos():
    banco = BancoFake()
    ev = evento(casas=("pinnacle",))
    cli = ClienteFake({"soccer_epl": [ev]})
    rodar_ciclo(banco, cli, referencia.PERFIL, sports=("soccer_epl",))
    n_eventos_1 = sum(1 for (t, _) in banco.inseridos if t == "eventos")
    rodar_ciclo(banco, cli, referencia.PERFIL, sports=("soccer_epl",))
    n_eventos_2 = sum(1 for (t, _) in banco.inseridos if t == "eventos")
    assert n_eventos_1 == 1 and n_eventos_2 == 1  # get-or-create não duplica


def test_sport_que_falha_nao_derruba_o_ciclo():
    banco = BancoFake()
    cli = ClienteFake({
        "soccer_epl": [evento(casas=("pinnacle",))],
        "soccer_spain_la_liga": OddsAPIError("timeout"),
    })
    r = rodar_ciclo(banco, cli, referencia.PERFIL,
                    sports=("soccer_epl", "soccer_spain_la_liga"))
    assert r.sports_ok == 1
    assert r.sports_falha == ["soccer_spain_la_liga"]
    assert r.snapshots == 7            # o sport bom foi capturado
    assert banco.pulsos[0][1]["sports_falha"] == ["soccer_spain_la_liga"]
