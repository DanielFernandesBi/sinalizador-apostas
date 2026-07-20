"""Testes da verificação de cobertura (E1 aceite #1) — fail-loud, sem adaptar."""
import pytest
from _fixtura_odds import evento

from sinalizador.l0_captura.cobertura import (
    CoberturaInsuficienteError,
    inspecionar,
    verificar_ou_parar,
)
from sinalizador.l0_captura.the_odds_api import RespostaOdds


class ClientePorRegiao:
    def __init__(self, por_regiao, custo=1, restantes=498):
        self.por_regiao = por_regiao
        self.custo = custo
        self.restantes = restantes

    def buscar_odds(self, sport, *, regions, markets):
        return RespostaOdds(eventos=self.por_regiao.get(regions, []),
                            requests_remaining=self.restantes, requests_used=2,
                            custo_ultima=self.custo, regioes=regions, mercados=markets)


def test_inspeciona_separa_casas_por_regiao():
    cli = ClientePorRegiao({
        "eu": [evento(casas=("pinnacle", "williamhill"))],
        "br": [evento(casas=("bet365_br", "betano_br"))],
    })
    cob = inspecionar(cli, sport="soccer_epl")
    assert cob.casas_eu == {"pinnacle", "williamhill"}
    assert cob.casas_br == {"bet365_br", "betano_br"}
    assert cob.custo_creditos == 2      # 1 (eu) + 1 (br)
    assert "Pinnacle presente: SIM" in cob.relatorio()


def test_cobertura_ok_nao_levanta():
    cli = ClientePorRegiao({
        "eu": [evento(casas=("pinnacle",))],
        "br": [evento(casas=("bet365_br",))],
    })
    verificar_ou_parar(inspecionar(cli, sport="soccer_epl"))  # não deve levantar


def test_pinnacle_ausente_em_eu_para():
    cli = ClientePorRegiao({
        "eu": [evento(casas=("williamhill",))],   # sem Pinnacle
        "br": [evento(casas=("bet365_br",))],
    })
    with pytest.raises(CoberturaInsuficienteError) as exc:
        verificar_ou_parar(inspecionar(cli, sport="soccer_epl"))
    assert "Pinnacle" in str(exc.value)


def test_br_sem_casas_para():
    cli = ClientePorRegiao({
        "eu": [evento(casas=("pinnacle",))],
        "br": [],   # nenhuma casa .bet.br
    })
    with pytest.raises(CoberturaInsuficienteError) as exc:
        verificar_ou_parar(inspecionar(cli, sport="soccer_epl"))
    assert "br" in str(exc.value)
