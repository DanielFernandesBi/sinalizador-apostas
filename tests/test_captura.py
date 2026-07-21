"""Testes do ciclo de captura (E1): snapshots em lote, heartbeat e créditos."""
from _fixtura_odds import evento

from sinalizador.l0_captura import referencia, varejo
from sinalizador.l0_captura.captura import rodar_ciclo
from sinalizador.l0_captura.the_odds_api import OddsAPIError, RespostaOdds


class BancoFake:
    def __init__(self, casas=None):
        self.eventos = {}
        self.casas = casas or {"pinnacle": {"id": "casa-pinn"}}
        self.inseridos = []
        self.lotes = []            # (tabela, [registros]) de inserir_muitos
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

    def inserir_muitos(self, tabela, registros):
        self.lotes.append((tabela, registros))
        linhas = []
        for reg in registros:
            self._seq += 1
            linhas.append({"id": f"{tabela}-{self._seq}", **reg})
        return linhas

    def pulsar(self, daemon, detalhe=None):
        self.pulsos.append((daemon, detalhe))

    def snapshots(self):
        return [r for (t, regs) in self.lotes if t == "odds_snapshots" for r in regs]

    def casas_criadas(self):
        return [reg for (t, reg) in self.inseridos if t == "casas"]


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


def test_eu_captura_todas_as_casas_classificadas_em_lote():
    # Sugestão nº 6: a mesma resposta da eu traz Pinnacle + exchange + varejo —
    # captura-se TODAS, classificadas, em UM POST (inserir_muitos).
    banco = BancoFake()
    cli = ClienteFake({"soccer_epl": [evento(casas=("pinnacle", "betfair_ex_eu", "bet365_br"))]})
    r = rodar_ciclo(banco, cli, referencia.PERFIL, sports=("soccer_epl",))

    # 3 casas × (h2h 3 + ou 2 + ah 2) = 21 snapshots, 1 evento, 3 casas
    assert r.snapshots == 21 and r.eventos == 1 and r.casas_vistas == 3
    assert len(banco.lotes) == 1 and banco.lotes[0][0] == "odds_snapshots"  # 1 POST, não 21
    assert len(banco.lotes[0][1]) == 21
    # betfair_ex_eu criada como exchange 6,5%; bet365_br como varejo 0
    criadas = {c["nome"]: c for c in banco.casas_criadas()}
    assert criadas["betfair_ex_eu"]["tipo"] == "exchange"
    assert criadas["betfair_ex_eu"]["comissao_pct"] == 6.5
    assert criadas["bet365_br"]["tipo"] == "varejo" and criadas["bet365_br"]["comissao_pct"] == 0.0
    assert r.por_tipo == {"referencia": 1, "exchange": 1, "varejo": 1}
    assert cli.chamadas == [("soccer_epl", "eu", "h2h,spreads,totals")]
    assert banco.pulsos[0][0] == "l0_referencia"
    assert banco.pulsos[0][1]["custo_creditos"] == 3
    assert banco.pulsos[0][1]["casas_por_tipo"] == {"referencia": 1, "exchange": 1, "varejo": 1}


def test_varejo_registra_casas_novas():
    banco = BancoFake()  # só pinnacle seedada
    cli = ClienteFake({"soccer_epl": [evento(casas=("bet365_br", "betano_br"))]})
    r = rodar_ciclo(banco, cli, varejo.PERFIL, sports=("soccer_epl",))

    assert r.snapshots == 14 and r.casas_vistas == 2
    casas_criadas = [reg["nome"] for reg in banco.casas_criadas()]
    assert set(casas_criadas) == {"bet365_br", "betano_br"}
    assert all(reg["tipo"] == "varejo" for reg in banco.casas_criadas())
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
