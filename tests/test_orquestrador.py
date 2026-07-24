"""Testes do wiring L0→L1 (orquestrador): snapshots reais → sinal/aborto."""
from datetime import datetime, timedelta, timezone

from sinalizador.l1_gatilhos.devig import devig_shin
from sinalizador.l1_gatilhos.edge import odd_minima_aceitavel
from sinalizador.l1_gatilhos.orquestrador import PoliticaVenue, rodar_l1

AGORA = datetime(2026, 7, 20, 20, 0, 30, tzinfo=timezone.utc)
T = "2026-07-20T20:00:10Z"   # dentro de sincronia (0s) e idade (20s)

_GATES = {
    "janela_sincronia_s": 60, "snapshot_idade_max_s": 600, "odd_teto": 3.30,
    "edge_min_pct": 2.0, "liquidez_multiplo_stake": 10, "janela_drop_s": 900,
    "drop_min_pct": 3.0, "anomalia_move_pct": 3.0, "kelly_fracao": 0.25,
    "stake_max_pct": 2.0, "rastreio_edge_min_pct": 1.0,
    "exposicao_max_jogo_pct": 3.0, "exposicao_max_liga_dia_pct": 6.0,
    "exposicao_max_dia_pct": 10.0,
}


class GatesFake:
    def get(self, nome):
        return _GATES[nome]


CASAS = [
    {"id": "c-pin", "nome": "pinnacle", "tipo": "referencia", "comissao_pct": 0, "ativa": True},
    {"id": "c-bf", "nome": "betfair_exchange", "tipo": "exchange", "comissao_pct": 6.5, "ativa": True},
    {"id": "c-bf2", "nome": "betfair2", "tipo": "exchange", "comissao_pct": 6.5, "ativa": True},
    {"id": "c-b365", "nome": "bet365_br", "tipo": "varejo", "comissao_pct": 0, "ativa": True},
]
EVENTOS = [{"id": "ev1", "liga": "Premier League", "mandante": "A", "visitante": "B",
            "inicio_utc": "2026-07-20T21:00:00Z"}]

REF_1X2 = [("1", 2.0), ("X", 3.5), ("2", 4.0)]


def _p1():
    probs, _ = devig_shin([o for _, o in REF_1X2])
    return probs[0]


class BancoFake:
    def __init__(self, snaps, *, banca=1000.0, exposicao=None, banca_papel=None, kill_switch=False):
        self._snaps = snaps
        self._banca = banca
        self._exposicao = exposicao or []
        self._banca_papel = banca_papel   # valor (str) da config_sistema, ou None
        self._kill_switch = kill_switch   # espelha vw_banca.kill_switch (P9)
        self.inseridos = []
        self.pulsos = []

    def config_vigente(self, chave):
        if chave == "banca_papel" and self._banca_papel is not None:
            return {"chave": chave, "valor": self._banca_papel, "vigente": True}
        return None

    def snapshots_desde(self, ts_iso):
        return self._snaps

    def casas_ativas(self):
        return CASAS

    def eventos_por_ids(self, ids):
        return [e for e in EVENTOS if e["id"] in ids]

    def banca_atual(self):
        # vw_banca só tem linha quando há ledger; carrega o kill_switch (P9).
        return {"saldo": self._banca, "kill_switch": self._kill_switch} if self._banca is not None else None

    def exposicao_aberta(self):
        return self._exposicao

    def inserir(self, tabela, registro):
        self.inseridos.append((tabela, registro))
        return {"id": f"{tabela}-{len(self.inseridos)}", **registro}

    def pulsar(self, daemon, detalhe=None):
        self.pulsos.append((daemon, detalhe))

    def por_tabela(self, tabela):
        return [r for (t, r) in self.inseridos if t == tabela]


def _snap(sel, odd, casa_id, *, liquidez=None, ts=T, linha=None, mercado="1x2"):
    return {"evento_id": "ev1", "casa_id": casa_id, "mercado": mercado, "selecao": sel,
            "linha": linha, "odd": odd, "liquidez": liquidez, "ts_fonte": ts, "ts_captura": ts}


def _ref_snaps():
    return [_snap(sel, odd, "c-pin") for sel, odd in REF_1X2]


def test_sinal_ponta_a_ponta_exchange():
    p1 = _p1()
    odd_venue = round(odd_minima_aceitavel(p1, 0.065, 0.02) + 0.15, 3)  # edge > 2%
    snaps = _ref_snaps() + [_snap("1", odd_venue, "c-bf", liquidez=100000)]
    banco = BancoFake(snaps)
    r = rodar_l1(banco, GatesFake(), agora=AGORA, politica=PoliticaVenue.EXCHANGE)

    assert r.sinais == 1 and r.abortos == 0
    sinal = banco.por_tabela("sinais")[0]
    assert sinal["gatilho"] == "value_bet" and sinal["mercado"] == "1x2"
    assert sinal["selecao"] == "1" and sinal["casa_venue_id"] == "c-bf"
    assert sinal["edge_liquido_pct"] >= 2.0
    assert banco.pulsos[0][0] == "l1"
    # Sugestão nº 8: em exchange a liquidez é aplicável e o gate passou (sinal só
    # nasce após aprovação); nada de marca sombra.
    liq = sinal["dossie"]["liquidez"]
    assert liq["liquidez_aplicavel"] is True and liq["gate_liquidez_ok"] is True
    assert "sombra_varejo" not in liq


def test_near_miss_edge_gera_aborto_com_clv_rastrear():
    p1 = _p1()
    odd_baixa = round((odd_minima_aceitavel(p1, 0.065, 0.01)
                       + odd_minima_aceitavel(p1, 0.065, 0.02)) / 2, 3)  # edge ~1,5%
    snaps = _ref_snaps() + [_snap("1", odd_baixa, "c-bf", liquidez=100000)]
    banco = BancoFake(snaps)
    r = rodar_l1(banco, GatesFake(), agora=AGORA, politica=PoliticaVenue.EXCHANGE)

    assert r.sinais == 0 and r.abortos == 1 and r.rastreados_clv == 1
    aborto = banco.por_tabela("abortos_l1")[0]
    assert aborto["gate_reprovado"] == "edge_min_pct"
    assert aborto["clv_rastrear"] is True


def test_odd_acima_do_teto_aborta_por_odd_teto():
    snaps = _ref_snaps() + [_snap("1", 4.00, "c-bf", liquidez=100000)]  # 4.0 > 3.30
    banco = BancoFake(snaps)
    r = rodar_l1(banco, GatesFake(), agora=AGORA, politica=PoliticaVenue.EXCHANGE)
    assert r.sinais == 0 and r.abortos == 1
    assert banco.por_tabela("abortos_l1")[0]["gate_reprovado"] == "odd_teto"


def test_referencia_incompleta_e_pulada():
    p1 = _p1()
    odd_venue = round(odd_minima_aceitavel(p1, 0.065, 0.02) + 0.15, 3)
    # falta a seleção "2" na referência → sem devig
    snaps = [_snap("1", 2.0, "c-pin"), _snap("X", 3.5, "c-pin"),
             _snap("1", odd_venue, "c-bf", liquidez=100000)]
    banco = BancoFake(snaps)
    r = rodar_l1(banco, GatesFake(), agora=AGORA, politica=PoliticaVenue.EXCHANGE)
    assert r.sinais == 0 and r.abortos == 0
    assert any("referência incompleta" in m for m in r.pulados)


def test_line_shopping_escolhe_o_maior_preco():
    p1 = _p1()
    odd_ok = round(odd_minima_aceitavel(p1, 0.065, 0.02) + 0.10, 3)
    snaps = _ref_snaps() + [
        _snap("1", odd_ok, "c-bf", liquidez=100000),
        _snap("1", odd_ok + 0.20, "c-bf2", liquidez=100000),  # melhor preço
    ]
    banco = BancoFake(snaps)
    rodar_l1(banco, GatesFake(), agora=AGORA, politica=PoliticaVenue.EXCHANGE)
    sinal = banco.por_tabela("sinais")[0]
    assert sinal["casa_venue_id"] == "c-bf2"
    assert len(sinal["dossie"]["venues_comparados"]) == 2


def test_exchange_puro_sem_exchange_nao_gera_sinal():
    p1 = _p1()
    odd_venue = round(odd_minima_aceitavel(p1, 0.065, 0.02) + 0.15, 3)
    # só venue de varejo capturado; política exchange → nenhum venue elegível
    snaps = _ref_snaps() + [_snap("1", odd_venue, "c-b365")]
    banco = BancoFake(snaps)
    r = rodar_l1(banco, GatesFake(), agora=AGORA, politica=PoliticaVenue.EXCHANGE)
    assert r.sinais == 0 and r.abortos == 0
    assert any("sem venue" in m for m in r.pulados)


def test_retail_sombra_gera_sinal_e_marca_desvio():
    p1 = _p1()
    odd_venue = round(odd_minima_aceitavel(p1, 0.0, 0.02) + 0.15, 3)  # varejo comissão 0
    snaps = _ref_snaps() + [_snap("1", odd_venue, "c-b365")]  # varejo, sem liquidez
    banco = BancoFake(snaps)
    r = rodar_l1(banco, GatesFake(), agora=AGORA, politica=PoliticaVenue.RETAIL_SOMBRA)
    assert r.sinais == 1  # gate de liquidez inaplicável a varejo
    dossie = banco.por_tabela("sinais")[0]["dossie"]
    # Sugestão nº 8: inaplicável ≠ reprovado. gate_liquidez_ok=None (não avaliado),
    # nunca False — senão o V-A5 do L2 vetaria todo sinal sombra.
    assert dossie["liquidez"]["liquidez_aplicavel"] is False
    assert dossie["liquidez"]["gate_liquidez_ok"] is None
    assert dossie["liquidez"]["sombra_varejo"] is True


def test_anomalia_marca_caminho_profundo():
    p1 = _p1()
    odd_base = odd_minima_aceitavel(p1, 0.065, 0.02) + 0.15
    t0 = "2026-07-20T19:55:10Z"   # ~5 min antes; dentro da janela_drop (900s)
    # venue moveu +4% (>= anomalia 3%); referência parada (1 ponto → move 0)
    snaps = _ref_snaps() + [
        _snap("1", round(odd_base, 3), "c-bf", liquidez=100000, ts=t0),
        _snap("1", round(odd_base * 1.04, 3), "c-bf", liquidez=100000, ts=T),
    ]
    banco = BancoFake(snaps)
    rodar_l1(banco, GatesFake(), agora=AGORA, politica=PoliticaVenue.EXCHANGE)
    sinal = banco.por_tabela("sinais")[0]
    assert sinal["gatilho_anomalo"] is True
    assert sinal["dossie"]["caminho"] == "profundo"


def test_ah_mandante_e_visitante_casam_no_mesmo_grupo_geram_sinal():
    # achado 5: com a linha CANÔNICA (perspectiva do mandante), mandante(-0.5) e
    # visitante(-0.5) caem no MESMO grupo (evento, mercado, linha) → book de AH
    # completo → devig 2-way → sinal. Antes o visitante ficava em +0.5, em grupo
    # separado, e o book nunca fechava (86/91 incompletos na auditoria).
    p_mand = devig_shin([1.90, 1.95])[0][0]              # (probs, z) → prob do mandante
    odd_venue = round(odd_minima_aceitavel(p_mand, 0.065, 0.02) + 0.15, 3)  # edge > 2%
    snaps = [
        _snap("mandante", 1.90, "c-pin", linha=-0.5, mercado="ah"),
        _snap("visitante", 1.95, "c-pin", linha=-0.5, mercado="ah"),   # canônica (fonte dava +0.5)
        _snap("mandante", odd_venue, "c-bf", liquidez=100000, linha=-0.5, mercado="ah"),
    ]
    banco = BancoFake(snaps)
    r = rodar_l1(banco, GatesFake(), agora=AGORA, politica=PoliticaVenue.EXCHANGE)
    assert r.sinais == 1                                  # o book fechou (senão seria 0)
    assert not any("referência incompleta" in m for m in r.pulados)
    sinal = banco.por_tabela("sinais")[0]
    assert sinal["mercado"] == "ah" and sinal["selecao"] == "mandante"
    assert sinal["linha"] == -0.5


def test_kill_switch_suspende_emissao():
    # P9 (achado 4): drawdown ≥ suspensão → o L1 NÃO emite sinais, mesmo com um
    # sinal que passaria em tudo. A captura/CLV seguem (fora do L1); só a emissão para.
    p1 = _p1()
    odd_venue = round(odd_minima_aceitavel(p1, 0.065, 0.02) + 0.15, 3)
    snaps = _ref_snaps() + [_snap("1", odd_venue, "c-bf", liquidez=100000)]
    banco = BancoFake(snaps, kill_switch=True)
    r = rodar_l1(banco, GatesFake(), agora=AGORA, politica=PoliticaVenue.EXCHANGE)
    assert r.sinais == 0 and r.abortos == 0
    assert banco.por_tabela("sinais") == []               # nada enfileirado
    assert banco.pulsos[-1][1]["motivo"] == "kill_switch"  # pulsou o motivo


def test_sem_banca_real_nem_papel_nao_gera_nada():
    p1 = _p1()
    odd_venue = round(odd_minima_aceitavel(p1, 0.065, 0.02) + 0.15, 3)
    snaps = _ref_snaps() + [_snap("1", odd_venue, "c-bf", liquidez=100000)]
    banco = BancoFake(snaps, banca=None)  # ledger vazio E sem banca_papel na config
    r = rodar_l1(banco, GatesFake(), agora=AGORA, politica=PoliticaVenue.EXCHANGE)
    assert r.sinais == 0 and r.abortos == 0
    assert banco.pulsos[0][1]["motivo"] == "sem_banca"


def test_banca_de_papel_dimensiona_quando_ledger_vazio():
    # Sugestão nº 7: ledger real vazio → usa banca_papel; dossiê marca banca=papel.
    p1 = _p1()
    odd_venue = round(odd_minima_aceitavel(p1, 0.0, 0.02) + 0.15, 3)  # varejo comissão 0
    snaps = _ref_snaps() + [_snap("1", odd_venue, "c-b365")]          # venue de varejo
    banco = BancoFake(snaps, banca=None, banca_papel="1000")
    r = rodar_l1(banco, GatesFake(), agora=AGORA, politica=PoliticaVenue.RETAIL_SOMBRA)
    assert r.sinais == 1
    sinal = banco.por_tabela("sinais")[0]
    assert sinal["dossie"]["banca_origem"] == "papel"
    assert sinal["stake_pct"] > 0  # dimensionou sobre a banca de papel
    assert banco.pulsos[-1][1]["banca_origem"] == "papel"
