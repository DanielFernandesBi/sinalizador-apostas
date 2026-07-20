"""Testes do cliente The Odds API (E1) — parsing, créditos e falha-alto (P6)."""
import json

import pytest

from sinalizador.l0_captura.the_odds_api import (
    ClienteOddsAPI,
    OddsAPIError,
    RespostaHTTP,
)


def _resp(status=200, corpo=b"[]", **headers):
    h = {k.replace("_", "-"): str(v) for k, v in headers.items()}
    return RespostaHTTP(status=status, headers=h, corpo=corpo)


def _cliente(resp):
    return ClienteOddsAPI("chave-x", transporte=lambda url: resp)


def test_chave_ausente_falha_alto():
    with pytest.raises(OddsAPIError):
        ClienteOddsAPI("")


def test_parseia_eventos_e_creditos():
    corpo = json.dumps([{"id": "ev1", "bookmakers": []}]).encode()
    cli = _cliente(_resp(corpo=corpo, **{
        "x-requests-remaining": 497, "x-requests-used": 3, "x-requests-last": 3,
    }))
    r = cli.buscar_odds("soccer_epl", regions="eu", markets="h2h,spreads,totals")
    assert len(r.eventos) == 1 and r.eventos[0]["id"] == "ev1"
    assert r.requests_remaining == 497
    assert r.requests_used == 3
    assert r.custo_ultima == 3
    assert r.regioes == "eu"


def test_status_nao_200_falha_alto_com_corpo():
    cli = _cliente(_resp(status=401, corpo=b'{"message":"invalid api key"}'))
    with pytest.raises(OddsAPIError) as exc:
        cli.buscar_odds("soccer_epl", regions="eu", markets="h2h")
    assert "401" in str(exc.value) and "invalid api key" in str(exc.value)


def test_corpo_nao_json_falha_alto():
    cli = _cliente(_resp(corpo=b"<html>oops</html>"))
    with pytest.raises(OddsAPIError):
        cli.buscar_odds("soccer_epl", regions="eu", markets="h2h")


def test_json_nao_lista_falha_alto():
    cli = _cliente(_resp(corpo=b'{"nao":"lista"}'))
    with pytest.raises(OddsAPIError):
        cli.buscar_odds("soccer_epl", regions="eu", markets="h2h")


def test_headers_ausentes_viram_none():
    cli = _cliente(_resp(corpo=b"[]"))
    r = cli.buscar_odds("soccer_epl", regions="br", markets="h2h")
    assert r.requests_remaining is None and r.custo_ultima is None
    assert r.eventos == []
