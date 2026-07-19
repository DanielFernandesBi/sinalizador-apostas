"""Testes do backtest (E6.1/E6.2) sobre fixture sintética (espelha colunas reais
do Football-Data). Sem rede — valida ingestão, replay, zero look-ahead, células.
"""
import pytest

from sinalizador.l1_gatilhos.devig import devig_shin
from backtest.football_data import carregar_partidas
from backtest.replay import (
    agregar_celulas,
    candidatos_da_partida,
    escrever_saidas,
    faixa_odd,
    replay,
)


def _partida_1x2(**over):
    # Pinnacle abertura/fechamento + casas de varejo. Venue H (B365=2.20) bate a
    # referência (PSH=2.00), então há value no H.
    row = {
        "Div": "E0", "_div": "E0", "_liga": "Inglaterra — Premier League",
        "Date": "10/08/2024", "HomeTeam": "A", "AwayTeam": "B", "FTR": "H",
        "PSH": "2.00", "PSD": "3.50", "PSA": "4.00",
        "PSCH": "1.90", "PSCD": "3.60", "PSCA": "4.30",   # fechamento (só medição)
        "B365H": "2.20", "B365D": "3.40", "B365A": "3.90",
        "BWH": "2.10", "BWD": "3.30", "BWA": "3.80",
        "WHH": "2.15", "WHD": "3.45", "WHA": "3.85",
    }
    row.update(over)
    return row


# ---- faixas de odd ----

@pytest.mark.parametrize("odd,esperado", [
    (1.20, "1.01-1.50"), (1.75, "1.50-2.00"), (2.20, "2.00-2.60"),
    (3.00, "2.60-3.30"), (4.00, "3.30-5.00"), (9.0, "5.00+"),
])
def test_faixa_odd(odd, esperado):
    assert faixa_odd(odd) == esperado


# ---- replay: candidato de value_bet ----

def test_candidato_1x2_usa_shin_e_edge_positivo():
    row = _partida_1x2()
    cands = candidatos_da_partida(row)
    # H deve ser candidato (venue 2.20 > preço justo de abertura)
    h = [c for c in cands if c["selecao"] == "H"]
    assert len(h) == 1
    c = h[0]
    # venue = melhor entre casas na abertura = max(2.20, 2.10, 2.15) = 2.20
    assert c["odd_venue"] == pytest.approx(2.20)
    # p_justa vem de Shin sobre a referência de abertura
    p_ab, _ = devig_shin([2.00, 3.50, 4.00])
    assert c["p_justa_abertura"] == pytest.approx(p_ab[0])
    assert c["edge_liquido"] > 0
    assert c["value_bet_provisional"] is True
    assert c["faixa_odd"] == "2.00-2.60"


def test_clv_medido_contra_fechamento_pinnacle():
    row = _partida_1x2()
    c = [x for x in candidatos_da_partida(row) if x["selecao"] == "H"][0]
    p_fe, _ = devig_shin([1.90, 3.60, 4.30])
    assert c["p_ref_fechamento"] == pytest.approx(p_fe[0])
    assert c["clv_pct"] == pytest.approx(c["odd_venue"] * p_fe[0] - 1.0)


def test_zero_look_ahead_decisao_independe_de_fechamento_e_resultado():
    base = candidatos_da_partida(_partida_1x2())
    # Muda TODO o fechamento e o resultado; decisão (edge, value_bet) não pode mudar.
    mexido = candidatos_da_partida(_partida_1x2(
        PSCH="1.50", PSCD="4.50", PSCA="7.00", FTR="A",  # fechamento válido, porém diferente
    ))
    by_sel = {c["selecao"]: c for c in base}
    by_sel_m = {c["selecao"]: c for c in mexido}
    assert set(by_sel) == set(by_sel_m)
    for sel in by_sel:
        assert by_sel_m[sel]["edge_liquido"] == pytest.approx(by_sel[sel]["edge_liquido"])
        assert by_sel_m[sel]["odd_venue"] == pytest.approx(by_sel[sel]["odd_venue"])
        assert by_sel_m[sel]["value_bet_provisional"] == by_sel[sel]["value_bet_provisional"]
        # só a MEDIÇÃO muda:
        assert by_sel_m[sel]["clv_pct"] != pytest.approx(by_sel[sel]["clv_pct"])


def test_referencia_incompleta_pula_mercado_sem_chutar():
    # Falta PSA → mercado 1x2 não é avaliado (P6: não interpola).
    row = _partida_1x2(PSA="")
    assert candidatos_da_partida(row) == []


def test_fechamento_incompleto_descarta_mercado():
    row = _partida_1x2(PSCA="")
    assert candidatos_da_partida(row) == []


# ---- agregação em células e P12 ----

def test_agregacao_celulas_e_amostra_insuficiente():
    cands = [
        {"liga": "L", "mercado": "1x2", "faixa_odd": "2.00-2.60",
         "clv_pct": 0.02, "value_bet_provisional": True},
        {"liga": "L", "mercado": "1x2", "faixa_odd": "2.00-2.60",
         "clv_pct": 0.04, "value_bet_provisional": True},
        {"liga": "L", "mercado": "1x2", "faixa_odd": "3.30-5.00",
         "clv_pct": -0.10, "value_bet_provisional": True},
        # não value_bet: não entra em célula alguma
        {"liga": "L", "mercado": "1x2", "faixa_odd": "2.00-2.60",
         "clv_pct": 9.9, "value_bet_provisional": False},
    ]
    celulas = agregar_celulas(cands, amostra_minima=2)
    por_faixa = {c["faixa_odd"]: c for c in celulas}
    assert por_faixa["2.00-2.60"]["n"] == 2
    assert por_faixa["2.00-2.60"]["clv_medio"] == pytest.approx(0.03)
    assert por_faixa["2.00-2.60"]["suficiente"] is True
    assert por_faixa["3.30-5.00"]["n"] == 1
    assert por_faixa["3.30-5.00"]["suficiente"] is False  # n < 200 (aqui < 2)


# ---- Asian Handicap (E6.2, mercado ah) ----

def _partida_ah(**over):
    row = {
        "Div": "E0", "_liga": "Inglaterra — Premier League", "Date": "10/08/2024",
        "HomeTeam": "A", "AwayTeam": "B", "FTHG": "2", "FTAG": "0",
        "AHh": "-0.5", "AHCh": "-0.5",            # linha de abertura == fechamento
        "PAHH": "1.90", "PAHA": "2.00",           # Pinnacle AH abertura
        "PAHCH": "1.85", "PAHCA": "2.05",         # Pinnacle AH fechamento
        "B365AHH": "2.05", "B365AHA": "1.85",     # venue (varejo)
    }
    row.update(over)
    return row


def test_ah_gera_candidato_com_linha_e_resultado():
    cands = [c for c in candidatos_da_partida(_partida_ah()) if c["mercado"] == "ah"]
    mand = [c for c in cands if c["selecao"] == "mandante"]
    assert len(mand) == 1
    c = mand[0]
    assert c["linha"] == -0.5
    assert c["odd_venue"] == pytest.approx(2.05)
    p_ab, _ = devig_shin([1.90, 2.00])
    assert c["p_justa_abertura"] == pytest.approx(p_ab[0])
    assert c["edge_liquido"] > 0
    # mandante -0.5 vence por 2 (2x0) → +1 (informativo, não usado no CLV)
    assert c["resultado_ah"] == 1.0


def test_ah_pulado_quando_linha_muda_entre_abertura_e_fechamento():
    cands = candidatos_da_partida(_partida_ah(AHCh="-0.75"))
    assert [c for c in cands if c["mercado"] == "ah"] == []


# ---- ingestão ----

def test_carregar_partidas_ignora_linhas_vazias():
    csv_text = (
        "Div,Date,HomeTeam,AwayTeam,PSH,PSD,PSA\n"
        "E0,10/08/2024,A,B,2.0,3.5,4.0\n"
        ",,,,,,\n"  # linha vazia (sem HomeTeam) → ignorada
    )
    partidas = carregar_partidas(csv_text)
    assert len(partidas) == 1
    assert partidas[0]["_liga"] == "Inglaterra — Premier League"


# ---- saídas: relatório declara limitações ----

def test_saidas_geradas_com_cabecalho_de_limitacoes(tmp_path):
    cands = replay([_partida_1x2()])
    celulas = agregar_celulas(cands)
    meta = {"gerado_em": "2026-07-19T00:00:00Z", "ligas": ["Inglaterra — Premier League"],
            "temporadas": ["2324"], "edge_min": 0.02, "odd_teto": 3.30, "amostra_minima": 200}
    escrever_saidas(cands, celulas, str(tmp_path), meta=meta)

    for nome in ("relatorio.md", "candidatos.csv", "celulas.csv", "celulas.json"):
        assert (tmp_path / nome).exists()

    rel = (tmp_path / "relatorio.md").read_text(encoding="utf-8")
    for termo in ["Zero look-ahead", "Dois instantes", "Brasileirão",
                  "amostra insuficiente", "comissão 0 e slippage 0"]:
        assert termo in rel
