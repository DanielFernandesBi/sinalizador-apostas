"""Testes da sonda OddsPapi (avaliação PC-VENUE) — parsing defensivo e report."""
import json

import pytest

from sinalizador.l0_captura.sonda_oddspapi import (
    SondaError,
    SondaOddsPapi,
    analisar,
)
from sinalizador.l0_captura.the_odds_api import RespostaHTTP


def _ev_padrao():
    return {
        "home_team": "A", "away_team": "B",
        "bookmakers": [
            {"key": "bet365", "last_update": "2026-07-20T20:00:00Z",
             "markets": [{"key": "h2h"}, {"key": "totals"}]},
            {"key": "betano", "last_update": "2026-07-20T20:01:00Z", "markets": [{"key": "h2h"}]},
            {"key": "pinnacle", "last_update": "2026-07-20T19:59:00Z", "markets": [{"key": "h2h"}]},
            {"key": "williamhill", "last_update": "2026-07-20T19:58:00Z", "markets": [{"key": "h2h"}]},
        ],
    }


def test_analisar_detecta_casas_br_pinnacle_e_mercados():
    rel = analisar([_ev_padrao()])
    assert "bet365" in rel.casas_brasileiras and "betano" in rel.casas_brasileiras
    assert "williamhill" not in rel.casas_brasileiras
    assert rel.pinnacle_presente is True
    assert set(rel.mercados) == {"h2h", "totals"}
    assert rel.timestamps[0] == "2026-07-20T20:01:00Z"  # mais recente primeiro
    assert rel.n_eventos == 1


def test_analisar_schema_alternativo():
    # convenções diferentes: 'books'/'name'/'updated_at'/'bets'
    ev = {"books": [
        {"name": "Betfair", "updated_at": "2026-07-20T20:00:00Z", "bets": [{"name": "h2h"}]},
    ]}
    rel = analisar([ev])
    assert rel.casas_brasileiras == ["betfair"]   # marca BR reconhecida
    assert rel.mercados == ["h2h"]
    assert rel.timestamps == ["2026-07-20T20:00:00Z"]


def test_relatorio_marca_experimental():
    rel = analisar([_ev_padrao()])
    assert "EXPERIMENTAL" in rel.relatorio() and "PC-VENUE" in rel.relatorio()


def test_buscar_lista_direta():
    corpo = json.dumps([_ev_padrao()]).encode()
    sonda = SondaOddsPapi("k", transporte=lambda url: RespostaHTTP(200, {}, corpo))
    evs = sonda.buscar("v1/odds", {"sport": "soccer"})
    assert len(evs) == 1


def test_buscar_desembrulha_data():
    corpo = json.dumps({"data": [_ev_padrao(), _ev_padrao()]}).encode()
    sonda = SondaOddsPapi("k", transporte=lambda url: RespostaHTTP(200, {}, corpo))
    assert len(sonda.buscar("v1/odds")) == 2


def test_buscar_status_nao_200_falha():
    sonda = SondaOddsPapi("k", transporte=lambda url: RespostaHTTP(403, {}, b"forbidden"))
    with pytest.raises(SondaError):
        sonda.buscar("v1/odds")


def test_chave_ausente_falha():
    with pytest.raises(SondaError):
        SondaOddsPapi("")
