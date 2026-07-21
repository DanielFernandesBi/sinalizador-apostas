"""Testes da cadência adaptativa do L0 (política pura + leitura de calendário)."""
from datetime import datetime, timedelta, timezone

from sinalizador.l0_captura import cadencia
from sinalizador.l0_captura.cadencia import (
    CadenciaConfig,
    intervalo_s,
    ler_calendario,
    parse_kickoff,
    planejar,
    sport_ativo,
)

AGORA = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)
CFG = CadenciaConfig()


def _em(horas):
    return AGORA + timedelta(hours=horas)


# ---------------- intervalo por proximidade ----------------

def test_intervalo_ultima_hora():
    assert intervalo_s([_em(0.5)], AGORA, CFG) == CFG.ultima_hora_s   # ≤ 1h → 5 min


def test_intervalo_pre_jogo():
    assert intervalo_s([_em(3)], AGORA, CFG) == CFG.pre_jogo_s        # ≤ 6h → 10 min


def test_intervalo_base_sem_jogo_proximo():
    assert intervalo_s([_em(30)], AGORA, CFG) == CFG.base_s           # > 6h → base


def test_intervalo_base_sem_jogo_futuro():
    assert intervalo_s([_em(-2)], AGORA, CFG) == CFG.base_s           # só passado → base
    assert intervalo_s([], AGORA, CFG) == CFG.base_s


def test_intervalo_usa_o_jogo_mais_proximo():
    # último jogo em 0,5h manda, mesmo com outros longe
    assert intervalo_s([_em(30), _em(0.5), _em(5)], AGORA, CFG) == CFG.ultima_hora_s


# ---------------- liga ativa (D+2) ----------------

def test_sport_ativo_com_jogo_em_d2():
    assert sport_ativo([_em(40)], AGORA, CFG) is True     # dentro de 48h
    assert sport_ativo([_em(60)], AGORA, CFG) is False    # além de 48h
    assert sport_ativo([], AGORA, CFG) is False
    assert sport_ativo([_em(-1)], AGORA, CFG) is False    # só passado


# ---------------- planejar ----------------

def test_planejar_intervalo_global_e_ligas_ativas():
    kickoffs = {
        "soccer_epl": [_em(0.5)],        # jogo já-já → dita o intervalo
        "soccer_spain_la_liga": [_em(40)],   # tem jogo em D+2 → ativa, mas longe
        "soccer_italy_serie_a": [_em(72)],   # além de D+2 → NÃO consulta (economia)
        "soccer_france_ligue_one": [],        # sem jogo → NÃO consulta
    }
    plano = planejar(kickoffs, AGORA, CFG)
    assert plano.intervalo_s == CFG.ultima_hora_s
    assert set(plano.sports) == {"soccer_epl", "soccer_spain_la_liga"}
    assert plano.proximidade_min_s == 1800.0


def test_planejar_jogos_todos_longe_ciclo_ocioso():
    # todos os jogos além de D+2 → base + nenhuma liga consultada (zero crédito),
    # mesmo havendo jogo futuro (só que distante).
    plano = planejar({"soccer_epl": [_em(100)], "soccer_spain_la_liga": []}, AGORA, CFG)
    assert plano.intervalo_s == CFG.base_s
    assert plano.sports == ()
    assert plano.proximidade_min_s == 100 * 3600.0


def test_planejar_sem_jogo_futuro_algum():
    plano = planejar({"soccer_epl": [_em(-3)], "soccer_spain_la_liga": []}, AGORA, CFG)
    assert plano.intervalo_s == CFG.base_s
    assert plano.sports == ()
    assert plano.proximidade_min_s is None


# ---------------- parse ----------------

def test_parse_kickoff():
    assert parse_kickoff("2026-07-21T12:00:00Z") == AGORA
    naive = parse_kickoff("2026-07-21T12:00:00")
    assert naive == AGORA and naive.tzinfo is not None
    assert parse_kickoff("lixo") is None
    assert parse_kickoff(None) is None


# ---------------- leitura de calendário (custo 0) ----------------

class _RespEventos:
    def __init__(self, fixtures, custo=0):
        self.fixtures = fixtures
        self.custo_ultima = custo
        self.requests_remaining = 499


class ClienteCalendarioFake:
    def __init__(self, por_sport, falham=()):
        self.por_sport = por_sport
        self.falham = set(falham)
        self.chamadas = []

    def buscar_eventos(self, sport):
        self.chamadas.append(sport)
        if sport in self.falham:
            raise RuntimeError("timeout /events")
        return _RespEventos(self.por_sport.get(sport, []))


def test_ler_calendario_agrega_kickoffs_e_custo_zero():
    cli = ClienteCalendarioFake({
        "soccer_epl": [{"commence_time": "2026-07-21T13:00:00Z"},
                       {"commence_time": "2026-07-22T13:00:00Z"}],
        "soccer_spain_la_liga": [{"commence_time": "sem_data"}],  # inválido é descartado (P6)
    })
    kickoffs, custo = ler_calendario(cli, ("soccer_epl", "soccer_spain_la_liga"))
    assert custo == 0
    assert len(kickoffs["soccer_epl"]) == 2
    assert kickoffs["soccer_spain_la_liga"] == []


def test_ler_calendario_sport_que_falha_vira_vazio():
    cli = ClienteCalendarioFake({"soccer_epl": [{"commence_time": "2026-07-21T13:00:00Z"}]},
                                falham=("soccer_italy_serie_a",))
    kickoffs, _ = ler_calendario(cli, ("soccer_epl", "soccer_italy_serie_a"))
    assert len(kickoffs["soccer_epl"]) == 1
    assert kickoffs["soccer_italy_serie_a"] == []   # degradação segura


# ---------------- endpoint /events do cliente ----------------

def test_cliente_buscar_eventos_parseia_e_le_custo():
    from sinalizador.l0_captura.the_odds_api import ClienteOddsAPI, RespostaHTTP

    corpo = b'[{"id": "e1", "commence_time": "2026-07-21T13:00:00Z", "home_team": "A", "away_team": "B"}]'
    chamadas = []

    def transporte(url):
        chamadas.append(url)
        return RespostaHTTP(status=200, headers={"x-requests-last": "0", "x-requests-remaining": "499"},
                            corpo=corpo)

    cli = ClienteOddsAPI("k", transporte=transporte)
    resp = cli.buscar_eventos("soccer_epl")
    assert len(resp.fixtures) == 1 and resp.fixtures[0]["id"] == "e1"
    assert resp.custo_ultima == 0 and resp.requests_remaining == 499
    assert "/sports/soccer_epl/events?" in chamadas[0]
