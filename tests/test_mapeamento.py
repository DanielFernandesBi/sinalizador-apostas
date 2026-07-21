"""Testes da tradução The Odds API → domínio (E1): evento, seleções, ts_fonte."""
from _fixtura_odds import evento

from sinalizador.l0_captura.mapeamento import (
    classificar_casa,
    iter_snapshots,
    liga_de,
    normalizar_evento,
)


def test_classificar_casa_referencia_exchange_varejo():
    # Sugestão nº 6: cada chave de bookmaker vira (tipo, comissao_pct).
    assert classificar_casa("pinnacle") == ("referencia", 0.0)
    assert classificar_casa("betfair_ex_eu") == ("exchange", 6.5)
    assert classificar_casa("betfair_ex_uk") == ("exchange", 6.5)
    # qualquer outra casa é varejo (venue do modo sombra), comissão não modelada
    assert classificar_casa("bet365_br") == ("varejo", 0.0)
    assert classificar_casa("betano") == ("varejo", 0.0)


def test_normaliza_evento_com_id_externo():
    ev = normalizar_evento(evento())
    assert ev["liga"] == "Premier League"
    assert ev["mandante"] == "Arsenal" and ev["visitante"] == "Chelsea"
    assert ev["inicio_utc"] == "2026-07-20T19:00:00Z"
    assert ev["ids_externos"] == {"odds_api": "ev1"}
    assert ev["esporte"] == "futebol"


def test_liga_desconhecida_cai_no_titulo():
    assert liga_de("soccer_zzz", "Liga Z") == "Liga Z"
    assert liga_de("soccer_zzz") == "soccer_zzz"


def _por_chave(snaps):
    return {(s["mercado"], s["selecao"]): s for s in snaps}


def test_snapshots_1x2_ou_ah_normalizados():
    snaps = list(iter_snapshots(evento()))
    m = _por_chave(snaps)
    # 1x2 → 1/X/2, sem linha
    assert m[("1x2", "1")]["odd"] == 2.10 and m[("1x2", "1")]["linha"] is None
    assert m[("1x2", "X")]["odd"] == 3.30
    assert m[("1x2", "2")]["odd"] == 3.40
    # ou → over/under com linha = point
    assert m[("ou", "over")]["odd"] == 1.90 and m[("ou", "over")]["linha"] == 2.5
    assert m[("ou", "under")]["linha"] == 2.5
    # ah → mandante/visitante com handicap
    assert m[("ah", "mandante")]["linha"] == -0.5
    assert m[("ah", "visitante")]["linha"] == 0.5


def test_ts_fonte_vem_do_mercado_e_cai_pro_bookmaker():
    ev = evento()
    # remove o last_update do primeiro mercado → cai para o do bookmaker
    ev["bookmakers"][0]["markets"][0].pop("last_update")
    snaps = _por_chave(iter_snapshots(ev))
    assert snaps[("1x2", "1")]["ts_fonte"] == "2026-07-20T18:30:05Z"  # bookmaker
    assert snaps[("ou", "over")]["ts_fonte"] == "2026-07-20T18:31:00Z"  # mercado


def test_sem_carimbo_de_fonte_descarta():
    ev = evento()
    bk = ev["bookmakers"][0]
    bk.pop("last_update")
    for mkt in bk["markets"]:
        mkt.pop("last_update", None)
    assert list(iter_snapshots(ev)) == []


def test_filtro_de_casa_referencia_so_pinnacle():
    ev = evento(casas=("pinnacle", "bet365_br"))
    so_pinn = list(iter_snapshots(ev, aceitar_casa=lambda k: k == "pinnacle"))
    assert {s["casa"] for s in so_pinn} == {"pinnacle"}
    todas = list(iter_snapshots(ev))
    assert {s["casa"] for s in todas} == {"pinnacle", "bet365_br"}


def test_outcome_desconhecido_descartado():
    ev = evento()
    ev["bookmakers"][0]["markets"][0]["outcomes"].append({"name": "Alienígena", "price": 9.0})
    snaps = list(iter_snapshots(ev))
    # o 1x2 continua com só 3 seleções válidas (o desconhecido é descartado)
    x2 = [s for s in snaps if s["mercado"] == "1x2"]
    assert len(x2) == 3
