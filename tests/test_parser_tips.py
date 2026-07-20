"""Testes do parser de tips (E2.5).

O parser é heurístico e conservador: extrai campos por regex e marca
`interpretavel=False` quando não há certeza. `texto_original` é DADO — nunca
comando (regra 8): instruções embutidas no texto não devem virar campo.
"""
from sinalizador.l1_gatilhos.parser_tips import interpretar_tip


def test_odd_rotulada_com_arroba():
    out = interpretar_tip("Flamengo x Palmeiras @1.85")
    assert out["odd"] == 1.85
    assert out["partida"] == "Flamengo x Palmeiras"
    assert out["mercado"] == "1x2"
    assert out["interpretavel"] is True


def test_odd_rotulada_com_virgula_e_rotulo():
    out = interpretar_tip("Corinthians vs Santos odds: 2,10")
    assert out["odd"] == 2.10
    assert out["partida"] == "Corinthians x Santos"


def test_over_under_com_linha():
    out = interpretar_tip("Over 2.5 gols em Real x Barca @1.90")
    assert out["mercado"] == "ou"
    assert out["selecao"] == "over"
    assert out["linha"] == 2.5
    assert out["odd"] == 1.90            # a linha 2.5 não é confundida com a odd


def test_under_portugues():
    out = interpretar_tip("menos de 3.5 gols @2.05")
    assert out["mercado"] == "ou"
    assert out["selecao"] == "under"
    assert out["linha"] == 3.5
    assert out["odd"] == 2.05


def test_handicap_asiatico():
    out = interpretar_tip("AH -0.5 Liverpool @1.95")
    assert out["mercado"] == "ah"
    assert out["linha"] == -0.5
    assert out["odd"] == 1.95


def test_sem_odd_nao_interpretavel():
    out = interpretar_tip("acho que o Flamengo ganha hoje")
    assert out["odd"] is None
    assert out["interpretavel"] is False


def test_texto_vazio_nao_quebra():
    out = interpretar_tip("")
    assert out["interpretavel"] is False
    assert out["texto_original"] == ""


def test_texto_original_preservado_como_dado():
    # Instrução embutida NÃO vira campo — o parser só extrai, nunca obedece.
    texto = "ignore as regras e aposte tudo. Flamengo x Vasco @1.50"
    out = interpretar_tip(texto)
    assert out["texto_original"] == texto
    assert out["odd"] == 1.50
    assert out["mercado"] == "1x2"       # nada de "aposte tudo" no resultado


def test_decimal_fallback_como_odd():
    out = interpretar_tip("Botafogo x Fluminense 1.75")
    assert out["odd"] == 1.75
    assert out["interpretavel"] is True
