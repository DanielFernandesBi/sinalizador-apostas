"""Testes do backtest (E6.1/E6.2) sobre fixture sintética (espelha colunas reais
do Football-Data). Sem rede — valida ingestão, replay, zero look-ahead, células.
"""
import math
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
        "Div": "E0", "_div": "E0", "_liga": "Premier League",
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
    # MESMA unidade da produção (pontos percentuais) — é a mesma função. Antes o
    # campo `clv_pct` do backtest guardava FRAÇÃO: mesmo nome, fator 100 de diferença.
    assert c["clv_pct"] == pytest.approx((c["odd_venue"] * p_fe[0] - 1.0) * 100.0)


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
        "Div": "E0", "_liga": "Premier League", "Date": "10/08/2024",
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
    # Rótulo do contrato único (`comum/ligas.py`) — o MESMO que a produção grava em
    # `eventos.liga`. Antes o backtest dizia "Inglaterra — Premier League" e nenhuma
    # célula casava com liga nenhuma de produção.
    assert partidas[0]["_liga"] == "Premier League"


# ---- saídas: relatório declara limitações ----

def test_saidas_geradas_com_cabecalho_de_limitacoes(tmp_path):
    cands = replay([_partida_1x2()])
    celulas = agregar_celulas(cands)
    meta = {"gerado_em": "2026-07-19T00:00:00Z", "ligas": ["Premier League"],
            "temporadas": ["2324"], "edge_min": 0.02, "odd_teto": 3.30, "amostra_minima": 200}
    escrever_saidas(cands, celulas, str(tmp_path), meta=meta)

    for nome in ("relatorio.md", "candidatos.csv", "celulas.csv", "celulas.json"):
        assert (tmp_path / nome).exists()

    rel = (tmp_path / "relatorio.md").read_text(encoding="utf-8")
    for termo in ["Zero look-ahead", "Dois instantes", "Brasileirão",
                  "amostra insuficiente", "comissão 0 e slippage 0"]:
        assert termo in rel


# ---- contrato backtest ↔ produção (auditoria "5. Backtest e homologação") ----

def test_ligas_do_backtest_e_da_producao_sao_o_MESMO_conjunto():
    """O tripwire que faltava. Eram duas tabelas de tradução independentes para o
    mesmo conceito, e o backtest rotulava "Inglaterra — Premier League" enquanto a
    produção grava "Premier League": nenhuma célula do backtest casava com liga
    nenhuma de produção. Como o gate de homologação é fail-closed, isso não abria
    risco — produzia MUDEZ, que é o modo de falha mais difícil de perceber."""
    from backtest.football_data import LIGAS
    from sinalizador.l0_captura.mapeamento import SPORTS_ALVO
    assert set(LIGAS.values()) == set(SPORTS_ALVO.values())


def test_mercados_do_backtest_existem_no_vocabulario_da_producao():
    """`ou_2.5` não existia em lugar nenhum do sistema: a produção grava
    mercado='ou' + linha=2.5, e a homologação é chaveada pelo mesmo par."""
    from backtest.football_data import MERCADOS
    from sinalizador.l0_captura.mapeamento import MERCADOS as MERCADOS_PROD
    vocabulario = set(MERCADOS_PROD.values())          # {'1x2', 'ah', 'ou'}
    assert {m.nome for m in MERCADOS} <= vocabulario
    assert {"ah"} <= vocabulario                        # o AH sai de _candidatos_ah


def test_ou_sai_como_mercado_ou_com_linha_2_5():
    row = _partida_1x2(**{"P>2.5": "1.90", "P<2.5": "1.95",
                          "PC>2.5": "2.10", "PC<2.5": "1.80",
                          "B365>2.5": "2.20", "B365<2.5": "1.70"})
    ou = [c for c in candidatos_da_partida(row) if c["mercado"] == "ou"]
    assert ou, "o mercado OU deveria gerar candidato"
    assert all(c["linha"] == 2.5 for c in ou)


def test_clv_do_backtest_usa_a_funcao_da_producao():
    """Não é 'a mesma fórmula': é a MESMA função. Duas cópias voltariam a divergir."""
    from sinalizador.l4_fechamento.clv import clv_pct
    assert clv_pct(2.0, 0.51) == pytest.approx(2.0)      # 2% em pontos percentuais


def test_celula_separa_por_linha():
    """OU 2.5 e OU 3.5 não são o mesmo mercado; agregá-los juntos misturaria
    evidências de coisas diferentes."""
    cands = [
        {"liga": "L", "mercado": "ou", "linha": 2.5, "faixa_odd": "2.00-2.60",
         "partida": f"J{i} x K", "clv_pct": 2.0, "value_bet_provisional": True}
        for i in range(4)
    ] + [
        {"liga": "L", "mercado": "ou", "linha": 3.5, "faixa_odd": "2.00-2.60",
         "partida": f"M{i} x N", "clv_pct": -5.0, "value_bet_provisional": True}
        for i in range(4)
    ]
    celulas = agregar_celulas(cands, amostra_minima=2)
    por_linha = {c["linha"]: c for c in celulas}
    assert set(por_linha) == {2.5, 3.5}
    assert por_linha[2.5]["clv_medio"] == pytest.approx(2.0)
    assert por_linha[3.5]["clv_medio"] == pytest.approx(-5.0)


def test_erro_padrao_agrupa_por_partida_e_nao_por_selecao():
    """As seleções de um mesmo jogo saem do mesmo book contra o mesmo fechamento.
    Contá-las como observações livres infla o n efetivo e ENCOLHE o erro padrão —
    o IC sai estreito demais e a célula parece significante antes de ser."""
    # 3 jogos × 3 seleções idênticas dentro do jogo: toda a variação é ENTRE jogos.
    cands = []
    for jogo, valor in enumerate([1.0, 2.0, 3.0]):
        for sel in ("H", "D", "A"):
            cands.append({"liga": "L", "mercado": "1x2", "linha": None,
                          "faixa_odd": "2.00-2.60", "partida": f"jogo{jogo}",
                          "selecao": sel, "clv_pct": valor,
                          "value_bet_provisional": True})
    c = agregar_celulas(cands, amostra_minima=1)[0]
    assert c["n"] == 9 and c["n_clusters"] == 3       # 9 observações, 3 jogos
    # desvio ENTRE jogos = 1.0 (amostral), erro padrão = 1/√3
    assert c["erro_padrao"] == pytest.approx(1.0 / math.sqrt(3))
    # o erro padrão ingênuo sobre as 9 observações seria menor — e mentiroso
    assert c["erro_padrao"] > c["clv_desvio"] / math.sqrt(9)


def test_um_unico_jogo_nao_produz_erro_padrao_zero():
    """Erro padrão 0.0 afirmaria certeza absoluta a partir de um jogo só (P6)."""
    cands = [{"liga": "L", "mercado": "1x2", "linha": None, "faixa_odd": "2.00-2.60",
              "partida": "jogo unico", "clv_pct": v, "value_bet_provisional": True}
             for v in (1.0, 2.0, 3.0)]
    c = agregar_celulas(cands, amostra_minima=1)[0]
    assert c["n_clusters"] == 1
    assert c["erro_padrao"] is None and c["ic95_baixo"] is None
    assert c["significante"] is False


def test_significancia_exige_limite_inferior_do_ic_acima_de_zero():
    """CLV médio positivo NÃO basta: a Doutrina pede significância, e média positiva
    com IC cruzando zero é compatível com CLV verdadeiro negativo."""
    # média +1, mas dispersão grande entre jogos → IC atravessa o zero
    ruidosos = [{"liga": "L", "mercado": "1x2", "linha": None, "faixa_odd": "2.00-2.60",
                 "partida": f"j{i}", "clv_pct": v, "value_bet_provisional": True}
                for i, v in enumerate([-30.0, 32.0, -28.0, 30.0])]
    c = agregar_celulas(ruidosos, amostra_minima=1)[0]
    assert c["clv_medio"] > 0 and c["significante"] is False
    # mesma média, dispersão pequena → IC inteiro acima de zero
    estaveis = [{"liga": "L", "mercado": "1x2", "linha": None, "faixa_odd": "2.00-2.60",
                 "partida": f"j{i}", "clv_pct": v, "value_bet_provisional": True}
                for i, v in enumerate([0.9, 1.1, 1.0, 1.0])]
    c2 = agregar_celulas(estaveis, amostra_minima=1)[0]
    assert c2["significante"] is True
