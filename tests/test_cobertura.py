"""Testes da cobertura (região eu) — fail-loud SÓ na Pinnacle; br não é região."""
import pytest
from _fixtura_odds import evento

from sinalizador.l0_captura.cobertura import (
    CoberturaInsuficienteError,
    inspecionar,
    verificar_ou_parar,
)
from sinalizador.l0_captura.the_odds_api import RespostaOdds


class ClienteEU:
    def __init__(self, eventos, custo=1, restantes=499):
        self.eventos = eventos
        self.custo = custo
        self.restantes = restantes

    def buscar_odds(self, sport, *, regions, markets):
        assert regions == "eu"   # cobertura só consulta eu (não há região br)
        return RespostaOdds(eventos=self.eventos, requests_remaining=self.restantes,
                            requests_used=1, custo_ultima=self.custo,
                            regioes=regions, mercados=markets)


def test_lista_por_jogo_e_destaca_pinnacle_e_betfair_ex():
    cli = ClienteEU([evento(casas=("pinnacle", "betfair_ex_eu", "williamhill"))])
    cob = inspecionar(cli, sport="soccer_epl")
    assert cob.pinnacle_presente is True
    assert cob.exchanges == ["betfair_ex_eu"]
    assert cob.casas_eu == {"pinnacle", "betfair_ex_eu", "williamhill"}
    assert cob.jogos[0].partida == "Arsenal x Chelsea"
    rel = cob.relatorio()
    assert "Pinnacle (referência): SIM" in rel
    assert "betfair_ex_eu" in rel
    assert "não tem região `br`" in rel


def test_pinnacle_presente_nao_levanta():
    cli = ClienteEU([evento(casas=("pinnacle",))])
    verificar_ou_parar(inspecionar(cli, sport="soccer_epl"))  # não deve levantar


def test_pinnacle_ausente_para():
    cli = ClienteEU([evento(casas=("williamhill", "betfair_ex_eu"))])  # sem Pinnacle
    with pytest.raises(CoberturaInsuficienteError) as exc:
        verificar_ou_parar(inspecionar(cli, sport="soccer_epl"))
    assert "Pinnacle" in str(exc.value)


def test_ausencia_de_varejo_nao_e_erro():
    # só Pinnacle (sem casas de varejo) — NÃO é erro (não há região br; é esperado)
    cli = ClienteEU([evento(casas=("pinnacle",))])
    cob = inspecionar(cli, sport="soccer_epl")
    verificar_ou_parar(cob)  # passa
    assert cob.exchanges == []
