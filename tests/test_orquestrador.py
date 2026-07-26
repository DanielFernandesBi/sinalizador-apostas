"""Testes do wiring L0→L1 (orquestrador): snapshots reais → sinal/aborto."""
from datetime import datetime, timedelta, timezone

from sinalizador.comum.tempo import para_datetime as _dt_iso

import pytest

from sinalizador.l1_gatilhos.devig import devig_shin
from sinalizador.l1_gatilhos.edge import odd_minima_aceitavel
from sinalizador.l1_gatilhos.orquestrador import PoliticaVenue, chave_candidato, rodar_l1

AGORA = datetime(2026, 7, 20, 20, 0, 30, tzinfo=timezone.utc)
T = "2026-07-20T20:00:10Z"   # dentro de sincronia (0s) e idade (20s)
T_ANT = "2026-07-20T19:50:10Z"  # revisão ANTERIOR da referência (dentro de janela_drop)

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
    {"id": "c-betano", "nome": "betano", "tipo": "varejo", "comissao_pct": 0, "ativa": True},
]
EVENTOS = [{"id": "ev1", "liga": "Premier League", "mandante": "A", "visitante": "B",
            "inicio_utc": "2026-07-20T21:00:00Z"}]

REF_1X2 = [("1", 2.0), ("X", 3.5), ("2", 4.0)]

# Homologação padrão (achado 8): os mercados usados nos testes de sinal já homologados
# (liga "Premier League"). Testes de sombra/não-autorização passam um mapa próprio.
HOMOLOG_PADRAO = {
    ("Premier League", "1x2"): "homologado",
    ("Premier League", "ah"): "homologado",
    ("Premier League", "ou"): "homologado",
}


def _p1():
    probs, _ = devig_shin([o for _, o in REF_1X2])
    return probs[0]


class BancoFake:
    def __init__(self, snaps, *, banca=1000.0, exposicao=None, banca_papel=None,
                 kill_switch=False, venues_exec=None, abertas=None, abortos=None,
                 homologados=None):
        self._snaps = snaps
        self._banca = banca
        self._exposicao = exposicao or []
        self._banca_papel = banca_papel   # valor (str) da config_sistema, ou None
        self._kill_switch = kill_switch   # espelha vw_banca.kill_switch (P9)
        self._venues_exec = venues_exec   # lista de chaves (allowlist), ou None
        self._abertas = set(abertas or ())   # chaves de candidato com sinal aberto (achado 7)
        self._abortos = set(abortos or ())   # chaves de candidato já abortadas na janela
        # achado 8: mapa (liga, mercado)→status de mercados_homologados. None = padrão
        # (mercados dos testes homologados). {} = nenhum homologado (falha de config).
        self._homologados = HOMOLOG_PADRAO if homologados is None else homologados
        self.inseridos = []
        self.pulsos = []
        self._chaves_usadas = set()
        self.agora_rpc = AGORA          # "now()" do banco, para a trava de kickoff

    def chaves_sinais_abertos(self):
        return set(self._abertas)

    def chaves_abortos_desde(self, ts_iso):
        return set(self._abortos)

    def homologacao_mercados(self):
        # Devolve o que foi injetado: mapa antigo (liga,mercado)→status OU a lista de
        # células da 0020. O núcleo lê as duas formas — o mapa É o caso "todos os
        # limites nulos", não um formato paralelo.
        if isinstance(self._homologados, dict):
            return dict(self._homologados)
        return [dict(c) for c in self._homologados]

    def config_vigente(self, chave):
        if chave == "banca_papel" and self._banca_papel is not None:
            return {"chave": chave, "valor": self._banca_papel, "vigente": True}
        if chave == "venues_executaveis" and self._venues_exec is not None:
            import json as _json
            return {"chave": chave, "valor": _json.dumps(self._venues_exec), "vigente": True}
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

    # RPCs guardadas (migration 0012). O fake espelha o essencial: recusa após o
    # apito e uma unidade por chave_candidato na vida do evento.
    def _guarda(self, registro, tabela):
        ev = next((e for e in EVENTOS if e["id"] == registro.get("evento_id")), None)
        inicio = _dt_iso(ev["inicio_utc"]) if ev else None
        if inicio is None or inicio <= self.agora_rpc:
            raise RuntimeError("partida já iniciada — nada é criado após o apito")
        chave = registro.get("chave_candidato")
        if chave and chave in self._chaves_usadas:
            return {"id": None, "criado": False, "motivo": "candidato_ja_registrado"}
        if chave:
            self._chaves_usadas.add(chave)
        self.inseridos.append((tabela, registro))
        return {"id": f"{tabela}-{len(self.inseridos)}", "criado": True, **registro}

    def registrar_sinal(self, registro):
        return self._guarda(registro, "sinais")

    def registrar_aborto(self, registro):
        return self._guarda(registro, "abortos_l1")

    def pulsar(self, daemon, detalhe=None):
        self.pulsos.append((daemon, detalhe))

    def por_tabela(self, tabela):
        return [r for (t, r) in self.inseridos if t == tabela]


def _snap(sel, odd, casa_id, *, liquidez=None, ts=T, linha=None, mercado="1x2"):
    return {"evento_id": "ev1", "casa_id": casa_id, "mercado": mercado, "selecao": sel,
            "linha": linha, "odd": odd, "liquidez": liquidez, "ts_fonte": ts, "ts_captura": ts}


def _ref_snaps(ts=(T_ANT, T)):
    """Referência com N revisões DISTINTAS e mesmas odds: movimento mensurável e
    nulo → referência comprovadamente parada. Uma revisão só (P0.4) não permite
    afirmar estabilidade e o candidato aborta como indeterminado."""
    return [_snap(sel, odd, "c-pin", ts=t) for t in ts for sel, odd in REF_1X2]


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
    snaps = [_snap("1", 2.0, "c-pin", ts=t) for t in (T_ANT, T)] + \
            [_snap("X", 3.5, "c-pin", ts=t) for t in (T_ANT, T)] + \
            [_snap("1", odd_venue, "c-bf", liquidez=100000)]
    banco = BancoFake(snaps)
    r = rodar_l1(banco, GatesFake(), agora=AGORA, politica=PoliticaVenue.EXCHANGE)
    assert r.sinais == 0 and r.abortos == 0
    assert any("sem revisão completa" in m for m in r.pulados)


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
    banco = BancoFake(snaps, venues_exec=["bet365_br"])  # allowlist (achado 6)
    r = rodar_l1(banco, GatesFake(), agora=AGORA, politica=PoliticaVenue.RETAIL_SOMBRA)
    assert r.sinais == 1  # gate de liquidez inaplicável a varejo
    dossie = banco.por_tabela("sinais")[0]["dossie"]
    # Sugestão nº 8: inaplicável ≠ reprovado. gate_liquidez_ok=None (não avaliado),
    # nunca False — senão o V-A5 do L2 vetaria todo sinal sombra.
    assert dossie["liquidez"]["liquidez_aplicavel"] is False
    assert dossie["liquidez"]["gate_liquidez_ok"] is None
    assert dossie["liquidez"]["sombra_varejo"] is True


def test_venue_sombra_so_casa_da_allowlist_vira_cartao():
    # achado 6: mesmo que uma casa FORA da allowlist tenha preço melhor, o venue do
    # cartão é a casa da allowlist. As de fora seguem no consenso (venues_comparados
    # / V-C2), nunca como venue do sinal.
    p1 = _p1()
    odd_allow = round(odd_minima_aceitavel(p1, 0.0, 0.02) + 0.10, 3)  # bet365_br (allowlist)
    odd_fora = round(odd_allow + 0.30, 3)                             # betano (melhor, fora)
    snaps = _ref_snaps() + [
        _snap("1", odd_allow, "c-b365"),      # executável
        _snap("1", odd_fora, "c-betano"),     # só observação de consenso
    ]
    banco = BancoFake(snaps, venues_exec=["bet365_br"])
    r = rodar_l1(banco, GatesFake(), agora=AGORA, politica=PoliticaVenue.RETAIL_SOMBRA)
    assert r.sinais == 1
    sinal = banco.por_tabela("sinais")[0]
    assert sinal["casa_venue_id"] == "c-b365"                # venue = casa da allowlist, não a melhor
    # as duas casas alimentam o consenso (line shopping), inclusive a de fora
    casas_consenso = {v["casa"] for v in sinal["dossie"]["venues_comparados"]}
    assert casas_consenso == {"bet365_br", "betano"}


def test_venue_sombra_sem_allowlist_nao_gera_sinal():
    # achado 6 (fail-closed): sem allowlist, nenhuma casa de varejo é executável →
    # nenhum sinal do modo sombra (não se sinaliza o que não se pode apostar).
    p1 = _p1()
    odd_venue = round(odd_minima_aceitavel(p1, 0.0, 0.02) + 0.15, 3)
    snaps = _ref_snaps() + [_snap("1", odd_venue, "c-b365")]
    banco = BancoFake(snaps)  # SEM venues_exec
    r = rodar_l1(banco, GatesFake(), agora=AGORA, politica=PoliticaVenue.RETAIL_SOMBRA)
    assert r.sinais == 0 and r.abortos == 0
    assert any("nenhum executável" in m for m in r.pulados)


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
    snaps = [_snap(sel, odd, "c-pin", linha=-0.5, mercado="ah", ts=t)
             for t in (T_ANT, T)
             for sel, odd in (("mandante", 1.90), ("visitante", 1.95))] + [
        _snap("mandante", odd_venue, "c-bf", liquidez=100000, linha=-0.5, mercado="ah"),
    ]
    banco = BancoFake(snaps)
    r = rodar_l1(banco, GatesFake(), agora=AGORA, politica=PoliticaVenue.EXCHANGE)
    assert r.sinais == 1                                  # o book fechou (senão seria 0)
    assert not any("sem revisão completa" in m for m in r.pulados)
    sinal = banco.por_tabela("sinais")[0]
    assert sinal["mercado"] == "ah" and sinal["selecao"] == "mandante"
    assert sinal["linha"] == -0.5


def test_sinal_carrega_chave_candidato():
    # achado 7: o sinal grava a chave determinística do candidato (unicidade no banco).
    p1 = _p1()
    odd_venue = round(odd_minima_aceitavel(p1, 0.065, 0.02) + 0.15, 3)
    snaps = _ref_snaps() + [_snap("1", odd_venue, "c-bf", liquidez=100000)]
    banco = BancoFake(snaps)
    rodar_l1(banco, GatesFake(), agora=AGORA, politica=PoliticaVenue.EXCHANGE)
    sinal = banco.por_tabela("sinais")[0]
    assert sinal["chave_candidato"] == chave_candidato("ev1", "1x2", None, "1", "c-bf")


def test_nao_reemite_sinal_ja_aberto():
    # achado 7: se já há um sinal ABERTO para o candidato, o L1 NÃO reemite.
    p1 = _p1()
    odd_venue = round(odd_minima_aceitavel(p1, 0.065, 0.02) + 0.15, 3)
    snaps = _ref_snaps() + [_snap("1", odd_venue, "c-bf", liquidez=100000)]
    chave = chave_candidato("ev1", "1x2", None, "1", "c-bf")
    banco = BancoFake(snaps, abertas={chave})
    r = rodar_l1(banco, GatesFake(), agora=AGORA, politica=PoliticaVenue.EXCHANGE)
    assert r.sinais == 0
    assert banco.por_tabela("sinais") == []
    assert any("já aberto" in m for m in r.pulados)


def test_nao_reregistra_aborto_duplicado():
    # achado 7: near-miss cujo candidato já foi abortado na janela não re-registra.
    p1 = _p1()
    odd_baixa = round((odd_minima_aceitavel(p1, 0.065, 0.01)
                       + odd_minima_aceitavel(p1, 0.065, 0.02)) / 2, 3)  # edge ~1,5% (near-miss)
    snaps = _ref_snaps() + [_snap("1", odd_baixa, "c-bf", liquidez=100000)]
    chave = chave_candidato("ev1", "1x2", None, "1", "c-bf")
    banco = BancoFake(snaps, abortos={chave})
    r = rodar_l1(banco, GatesFake(), agora=AGORA, politica=PoliticaVenue.EXCHANGE)
    assert r.abortos == 0
    assert banco.por_tabela("abortos_l1") == []
    assert any("aborto já registrado" in m for m in r.pulados)


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
    banco = BancoFake(snaps, banca=None, banca_papel="1000", venues_exec=["bet365_br"])
    r = rodar_l1(banco, GatesFake(), agora=AGORA, politica=PoliticaVenue.RETAIL_SOMBRA)
    assert r.sinais == 1
    sinal = banco.por_tabela("sinais")[0]
    assert sinal["dossie"]["banca_origem"] == "papel"
    assert sinal["stake_pct"] > 0  # dimensionou sobre a banca de papel
    assert banco.pulsos[-1][1]["banca_origem"] == "papel"


# ---- achado 8: gate de homologação de mercado (Doutrina P2) ----

def test_mercado_backtest_vira_candidato_sombra_nao_sinal():
    # achado 8: mercado em 'backtest' (calibração) que passa em TODOS os gates NÃO vira
    # sinal — vira candidato_sombra: aborto com gate 'mercado_nao_homologado' e
    # clv_rastrear=True (só CLV, alimenta E6.4), jamais confirmado/cartão (P2).
    p1 = _p1()
    odd_venue = round(odd_minima_aceitavel(p1, 0.065, 0.02) + 0.15, 3)  # edge > 2%
    snaps = _ref_snaps() + [_snap("1", odd_venue, "c-bf", liquidez=100000)]
    banco = BancoFake(snaps, homologados={("Premier League", "1x2"): "backtest"})
    r = rodar_l1(banco, GatesFake(), agora=AGORA, politica=PoliticaVenue.EXCHANGE)

    assert r.sinais == 0                       # nada enfileirado como sinal
    assert banco.por_tabela("sinais") == []
    assert r.candidatos_sombra == 1 and r.rastreados_clv == 1
    aborto = banco.por_tabela("abortos_l1")[0]
    assert aborto["gate_reprovado"] == "mercado_nao_homologado"
    assert aborto["clv_rastrear"] is True
    assert aborto["chave_candidato"] == chave_candidato("ev1", "1x2", None, "1", "c-bf")
    assert banco.pulsos[-1][1]["candidatos_sombra"] == 1


def test_mercado_sem_homologacao_e_falha_de_config_pula_grupo():
    # achado 8: sem linha em mercados_homologados = FALHA DE CONFIGURAÇÃO (fail-loud),
    # não licença para calibrar. O grupo é pulado (nem sinal, nem candidato_sombra);
    # marcador 'mercado_nao_configurado' no log de abortos (sem rastrear CLV).
    p1 = _p1()
    odd_venue = round(odd_minima_aceitavel(p1, 0.065, 0.02) + 0.15, 3)
    snaps = _ref_snaps() + [_snap("1", odd_venue, "c-bf", liquidez=100000)]
    banco = BancoFake(snaps, homologados={})   # nenhum mercado configurado
    r = rodar_l1(banco, GatesFake(), agora=AGORA, politica=PoliticaVenue.EXCHANGE)

    assert r.sinais == 0 and r.candidatos_sombra == 0
    assert r.nao_autorizados == 1 and r.rastreados_clv == 0
    assert banco.por_tabela("sinais") == []
    aborto = banco.por_tabela("abortos_l1")[0]
    assert aborto["gate_reprovado"] == "mercado_nao_configurado"
    assert aborto["clv_rastrear"] is False
    assert banco.pulsos[-1][1]["nao_autorizados"] == 1


def test_mercado_suspenso_nao_gera_sinal_nem_sombra():
    # achado 8: mercado 'suspenso' (retirada explícita) não opera — nem candidato_sombra.
    p1 = _p1()
    odd_venue = round(odd_minima_aceitavel(p1, 0.065, 0.02) + 0.15, 3)
    snaps = _ref_snaps() + [_snap("1", odd_venue, "c-bf", liquidez=100000)]
    banco = BancoFake(snaps, homologados={("Premier League", "1x2"): "suspenso"})
    r = rodar_l1(banco, GatesFake(), agora=AGORA, politica=PoliticaVenue.EXCHANGE)

    assert r.sinais == 0 and r.candidatos_sombra == 0 and r.nao_autorizados == 1
    assert banco.por_tabela("abortos_l1")[0]["gate_reprovado"] == "mercado_suspenso"


def test_candidato_sombra_deduplicado_na_janela():
    # achado 8 + 7: candidato_sombra cujo candidato já foi registrado na janela não
    # re-registra (um por ciclo, não um por minuto).
    p1 = _p1()
    odd_venue = round(odd_minima_aceitavel(p1, 0.065, 0.02) + 0.15, 3)
    snaps = _ref_snaps() + [_snap("1", odd_venue, "c-bf", liquidez=100000)]
    chave = chave_candidato("ev1", "1x2", None, "1", "c-bf")
    banco = BancoFake(snaps, homologados={("Premier League", "1x2"): "backtest"},
                      abortos={chave})
    r = rodar_l1(banco, GatesFake(), agora=AGORA, politica=PoliticaVenue.EXCHANGE)

    assert r.candidatos_sombra == 0 and r.abortos == 0
    assert banco.por_tabela("abortos_l1") == []
    assert any("candidato_sombra já registrado" in m for m in r.pulados)


# ---- P0.1: nada nasce depois do apito ----

def test_partida_ja_iniciada_nao_gera_nada():
    # O L1 lê a janela de lookback e uma revisão pré-jogo continua "fresca" pelo gate
    # de idade por até 600 s: sem a trava, minutos após o início ainda nasciam sinal,
    # aborto e candidato_sombra — e o L4 pode já ter finalizado o evento.
    p1 = _p1()
    odd_venue = round(odd_minima_aceitavel(p1, 0.065, 0.02) + 0.15, 3)
    snaps = _ref_snaps() + [_snap("1", odd_venue, "c-bf", liquidez=100000)]
    banco = BancoFake(snaps)
    depois = _dt_iso("2026-07-20T21:00:01Z")      # 1 s após o kickoff
    banco.agora_rpc = depois
    r = rodar_l1(banco, GatesFake(), agora=depois, politica=PoliticaVenue.EXCHANGE)

    assert r.sinais == 0 and r.abortos == 0 and r.candidatos_sombra == 0
    assert r.pos_kickoff == 1
    assert banco.inseridos == []                   # nem sinal, nem aborto
    assert any("já iniciada" in m for m in r.pulados)


def test_no_exato_instante_do_kickoff_ja_nao_cria():
    p1 = _p1()
    odd_venue = round(odd_minima_aceitavel(p1, 0.065, 0.02) + 0.15, 3)
    snaps = _ref_snaps() + [_snap("1", odd_venue, "c-bf", liquidez=100000)]
    banco = BancoFake(snaps)
    kickoff = _dt_iso("2026-07-20T21:00:00Z")
    banco.agora_rpc = kickoff
    r = rodar_l1(banco, GatesFake(), agora=kickoff, politica=PoliticaVenue.EXCHANGE)
    assert r.pos_kickoff == 1 and banco.inseridos == []


# ---- P0.3: o book da EMISSÃO também é uma revisão indivisível ----

def test_revisao_incompleta_nao_e_completada_com_a_anterior():
    # Às 20h00:20 a seleção "2" saiu do payload (suspensa) e "X" mudou de 3.50 para
    # 3.90. O p_justa NÃO pode sair de "1"/"X" dessa revisão parcial casados com o
    # "2" da anterior — book que nunca existiu. Vale a última revisão COMPLETA,
    # inteira. Se o de-vig tivesse usado X=3.90, o p_justa seria outro.
    p1 = _p1()
    odd_venue = round(odd_minima_aceitavel(p1, 0.065, 0.02) + 0.15, 3)
    t_pos = "2026-07-20T20:00:20Z"
    snaps = _ref_snaps() + [                       # completas em T_ANT e T
        _snap("1", 2.0, "c-pin", ts=t_pos),        # parcial: sem "2", com X alterado
        _snap("X", 3.90, "c-pin", ts=t_pos),
        _snap("1", odd_venue, "c-bf", liquidez=100000),
    ]
    banco = BancoFake(snaps)
    r = rodar_l1(banco, GatesFake(), agora=AGORA, politica=PoliticaVenue.EXCHANGE)

    assert r.sinais == 1, banco.por_tabela("abortos_l1")
    sinal = banco.por_tabela("sinais")[0]
    assert sinal["p_justa"] == pytest.approx(p1)   # veio da revisão completa
    assert sinal["odd_referencia"] == 2.0


def test_sem_revisao_completa_nao_emite():
    p1 = _p1()
    odd_venue = round(odd_minima_aceitavel(p1, 0.065, 0.02) + 0.15, 3)
    snaps = [_snap("1", 2.0, "c-pin"), _snap("X", 3.5, "c-pin"),   # nunca teve "2"
             _snap("1", odd_venue, "c-bf", liquidez=100000)]
    banco = BancoFake(snaps)
    r = rodar_l1(banco, GatesFake(), agora=AGORA, politica=PoliticaVenue.EXCHANGE)
    assert r.sinais == 0 and banco.por_tabela("sinais") == []
    assert any("sem revisão completa" in m for m in r.pulados)


# ---- P0.4: estabilidade é afirmação, não default ----

def test_uma_revisao_so_aborta_por_estabilidade_indeterminada():
    # Antes: sem histórico → variação 0.0 → "referência estável" → sinal emitido.
    p1 = _p1()
    odd_venue = round(odd_minima_aceitavel(p1, 0.065, 0.02) + 0.15, 3)
    snaps = _ref_snaps(ts=(T,)) + [_snap("1", odd_venue, "c-bf", liquidez=100000)]
    banco = BancoFake(snaps)
    r = rodar_l1(banco, GatesFake(), agora=AGORA, politica=PoliticaVenue.EXCHANGE)

    assert r.sinais == 0 and r.abortos == 1
    aborto = banco.por_tabela("abortos_l1")[0]
    assert aborto["gate_reprovado"] == "referencia_estabilidade_indeterminada"
    assert aborto["clv_rastrear"] is False     # não é near-miss: é dado insuficiente
    assert aborto["dossie_parcial"]["revisoes_na_janela"] == 1


# ---- P0.5 / P0.6: uma unidade estatística por aposta lógica ----

def test_candidato_ja_registrado_nao_reemite_mesmo_apos_veto():
    # A RPC devolve criado=False (o índice global recusou). O L1 não pode contar
    # isso como sinal novo — seria a mesma aposta entrando duas vezes na amostra.
    p1 = _p1()
    odd_venue = round(odd_minima_aceitavel(p1, 0.065, 0.02) + 0.15, 3)
    snaps = _ref_snaps() + [_snap("1", odd_venue, "c-bf", liquidez=100000)]
    banco = BancoFake(snaps)
    chave = chave_candidato("ev1", "1x2", None, "1", "c-bf")
    banco._chaves_usadas.add(chave)             # já existiu (vetado num ciclo anterior)
    r = rodar_l1(banco, GatesFake(), agora=AGORA, politica=PoliticaVenue.EXCHANGE)

    assert r.sinais == 0
    assert banco.por_tabela("sinais") == []


# ---------------- P0.8: exposição comprometida DENTRO do ciclo ----------------

REF_OU = [("over", 2.0), ("under", 2.0)]


def _ref_ou_snaps(ts=(T_ANT, T)):
    return [_snap(sel, odd, "c-pin", ts=t, linha=2.5, mercado="ou")
            for t in ts for sel, odd in REF_OU]


def _cenario_dois_candidatos_no_mesmo_jogo():
    """Dois mercados do MESMO evento, cada um com edge folgado o bastante para o
    stake bater o teto de `stake_max_pct` (2% de 1000 = 20). Teto por jogo = 3% = 30:
    o primeiro cabe, o segundo não."""
    snaps = (_ref_snaps() + _ref_ou_snaps()
             + [_snap("1", 2.60, "c-b365"),
                _snap("over", 2.60, "c-b365", linha=2.5, mercado="ou")])
    homolog = {("Premier League", "1x2"): "homologado",
               ("Premier League", "ou"): "homologado"}
    return BancoFake(snaps, banca=1000.0, venues_exec=["bet365_br"], homologados=homolog)


def test_segundo_sinal_do_mesmo_ciclo_ja_ve_o_teto_ocupado():
    """P0.8 — a view de exposição é lida UMA vez por ciclo. Sem o acumulador, os dois
    candidatos leem exposição zero e passam juntos: 40 de nocional num teto de 30."""
    banco = _cenario_dois_candidatos_no_mesmo_jogo()
    r = rodar_l1(banco, GatesFake(), agora=AGORA, politica=PoliticaVenue.RETAIL_SOMBRA)

    assert r.sinais == 1, f"esperava 1 sinal, veio {r.sinais}"
    abortos = banco.por_tabela("abortos_l1")
    assert [a["gate_reprovado"] for a in abortos] == ["exposicao_jogo"]


def test_teto_maior_deixa_os_dois_passarem():
    """Controle: o bloqueio acima é do TETO, não de um efeito colateral do
    acumulador. Com teto por jogo de 10% (100), os dois nocionais de 20 cabem."""
    gates = dict(_GATES, exposicao_max_jogo_pct=10.0, exposicao_max_liga_dia_pct=10.0)

    class GatesFolgados:
        def get(self, nome):
            return gates[nome]

    banco = _cenario_dois_candidatos_no_mesmo_jogo()
    r = rodar_l1(banco, GatesFolgados(), agora=AGORA, politica=PoliticaVenue.RETAIL_SOMBRA)
    assert r.sinais == 2 and r.abortos == 0


def test_dossie_grava_o_nocional_absoluto_e_a_banca():
    """A posição de papel é aberta pelo L3 a partir do DOSSIÊ: `stake_pct` sozinho é
    fração, e recompor o valor depois multiplicaria por uma banca que pode ter mudado."""
    p1 = _p1()
    odd_venue = round(odd_minima_aceitavel(p1, 0.0, 0.02) + 0.15, 3)
    snaps = _ref_snaps() + [_snap("1", odd_venue, "c-b365")]
    banco = BancoFake(snaps, banca=None, banca_papel="500", venues_exec=["bet365_br"])
    r = rodar_l1(banco, GatesFake(), agora=AGORA, politica=PoliticaVenue.RETAIL_SOMBRA)

    assert r.sinais == 1
    sinal = banco.por_tabela("sinais")[0]
    exp = sinal["dossie"]["exposicao"]
    assert exp["banca_valor"] == 500.0
    assert exp["stake_valor"] == pytest.approx(sinal["stake_pct"] / 100.0 * 500.0)
    assert exp["stake_valor"] > 0
    assert sinal["dossie"]["banca_origem"] == "papel"


def test_reservas_do_ciclo_nao_vazam_entre_jogos_ligas_e_dias():
    from sinalizador.l1_gatilhos.orquestrador import ReservasDoCiclo

    res = ReservasDoCiclo()
    base = {"jogo": 0.0, "liga_dia": 0.0, "dia": 0.0}
    res.somar("ev1", "Premier League", "2026-07-20", 20.0)

    assert res.sobre(base, "ev1", "Premier League", "2026-07-20") == {
        "jogo": 20.0, "liga_dia": 20.0, "dia": 20.0}
    # outro jogo da MESMA liga no MESMO dia: só o nível do jogo zera
    assert res.sobre(base, "ev2", "Premier League", "2026-07-20") == {
        "jogo": 0.0, "liga_dia": 20.0, "dia": 20.0}
    # outra liga no mesmo dia: só o nível do dia acumula
    assert res.sobre(base, "ev3", "La Liga", "2026-07-20") == {
        "jogo": 0.0, "liga_dia": 0.0, "dia": 20.0}
    # outro dia: nada acumula
    assert res.sobre(base, "ev4", "Premier League", "2026-07-21") == base
    # soma sobre o que já veio do banco
    assert res.sobre({"jogo": 5.0, "liga_dia": 5.0, "dia": 5.0},
                     "ev1", "Premier League", "2026-07-20")["jogo"] == 25.0


# ---------------- P1.1: elegibilidade ANTES do line shopping ----------------

T_VELHO = "2026-07-20T19:40:00Z"    # 1220 s antes de AGORA — fora de idade (600)


def test_odd_velha_maior_nao_rouba_a_vez_da_fresca_menor():
    """O cenário da auditoria: casa A com 2,20 VELHA e casa B com 2,12 fresca. Antes,
    A vencia o line shopping, reprovava no gate de idade e matava o candidato — B
    nunca era avaliada, em ciclo nenhum enquanto A fosse a maior."""
    p1 = _p1()
    odd_b = round(odd_minima_aceitavel(p1, 0.0, 0.02) + 0.10, 3)
    odd_a = odd_b + 0.08                                   # A é maior, porém velha
    snaps = _ref_snaps() + [
        _snap("1", odd_a, "c-b365", ts=T_VELHO),
        _snap("1", odd_b, "c-betano"),
    ]
    banco = BancoFake(snaps, venues_exec=["bet365_br", "betano"])
    r = rodar_l1(banco, GatesFake(), agora=AGORA, politica=PoliticaVenue.RETAIL_SOMBRA)

    assert r.sinais == 1, f"esperava 1 sinal, veio {r.sinais} ({r.pulados})"
    sinal = banco.por_tabela("sinais")[0]
    assert sinal["casa_venue_id"] == "c-betano"             # a fresca, não a maior
    assert float(sinal["odd_venue"]) == odd_b


def test_consenso_preserva_a_casa_inelegivel_marcada():
    """A inelegível não some do dossiê: apagá-la apagaria a evidência de que o line
    shopping a viu e por que a descartou."""
    p1 = _p1()
    odd_b = round(odd_minima_aceitavel(p1, 0.0, 0.02) + 0.10, 3)
    snaps = _ref_snaps() + [
        _snap("1", odd_b + 0.08, "c-b365", ts=T_VELHO),
        _snap("1", odd_b, "c-betano"),
    ]
    banco = BancoFake(snaps, venues_exec=["bet365_br", "betano"])
    rodar_l1(banco, GatesFake(), agora=AGORA, politica=PoliticaVenue.RETAIL_SOMBRA)

    comparados = {v["casa"]: v for v in
                  banco.por_tabela("sinais")[0]["dossie"]["venues_comparados"]}
    assert comparados["betano"]["elegivel"] is True
    assert comparados["bet365_br"]["elegivel"] is False
    assert comparados["bet365_br"]["motivo_inelegivel"] == "snapshot_velho"


def test_nenhum_venue_elegivel_e_contado_como_mudez_sem_virar_aborto():
    """Preço velho em TODAS as casas executáveis é falha de captura, não juízo de
    mercado: não vira aborto (inflaria o log a cada ciclo), mas fica contado — mudez
    silenciosa foi o achado."""
    p1 = _p1()
    odd = round(odd_minima_aceitavel(p1, 0.0, 0.02) + 0.10, 3)
    snaps = _ref_snaps() + [_snap("1", odd, "c-b365", ts=T_VELHO)]
    banco = BancoFake(snaps, venues_exec=["bet365_br"])
    r = rodar_l1(banco, GatesFake(), agora=AGORA, politica=PoliticaVenue.RETAIL_SOMBRA)

    assert r.sinais == 0 and r.abortos == 0
    assert r.venues_inelegiveis == 1
    assert any("nenhum com preço elegível" in m for m in r.pulados)
    assert banco.pulsos[0][1]["venues_inelegiveis"] == 1


def test_classificar_elegibilidade_nomeia_cada_motivo():
    from datetime import timedelta as _td

    from sinalizador.l1_gatilhos.gatilhos import classificar_elegibilidade

    ts_ref = AGORA - _td(seconds=20)
    venues = [
        {"casa": "ok", "odd": 2.10, "ts_fonte": AGORA - _td(seconds=10)},
        {"casa": "velha", "odd": 2.30, "ts_fonte": AGORA - _td(seconds=1200)},
        {"casa": "dessinc", "odd": 2.40, "ts_fonte": AGORA - _td(seconds=500)},
        {"casa": "odd_ruim", "odd": 1.0, "ts_fonte": AGORA},
        {"casa": "sem_ts", "odd": 2.50, "ts_fonte": None},
    ]
    por_casa = {v["casa"]: v for v in classificar_elegibilidade(
        venues, ts_referencia=ts_ref, agora=AGORA,
        idade_max_s=600.0, janela_sincronia_s=60.0)}

    assert por_casa["ok"]["elegivel"] is True
    assert por_casa["velha"]["motivo_inelegivel"] == "snapshot_velho"
    assert por_casa["dessinc"]["motivo_inelegivel"] == "dessincronizado_da_referencia"
    assert por_casa["odd_ruim"]["motivo_inelegivel"] == "odd_invalida"
    assert por_casa["sem_ts"]["motivo_inelegivel"] == "sem_carimbo_de_fonte"


# ---- homologação por CÉLULA (auditoria "5. Backtest e homologação", migration 0020) ----

def _celula(status, **over):
    base = {"liga": "Premier League", "mercado": "1x2", "linha": None,
            "odd_min": None, "odd_max": None, "status": status, "suspenso_em": None}
    base.update(over)
    return base


def _cenario_com_odd(celulas):
    """Um candidato de 1x2 que passa em todos os gates, com a odd conhecida."""
    p1 = _p1()
    odd_venue = round(odd_minima_aceitavel(p1, 0.065, 0.02) + 0.15, 3)
    snaps = _ref_snaps() + [_snap("1", odd_venue, "c-bf", liquidez=100000)]
    banco = BancoFake(snaps, homologados=celulas)
    r = rodar_l1(banco, GatesFake(), agora=AGORA, politica=PoliticaVenue.EXCHANGE)
    return odd_venue, banco, r


def test_faixa_homologada_dentro_de_mercado_em_calibracao_vira_sinal():
    """O caso que a tabela antiga não sabia representar: o mercado inteiro segue em
    calibração, MAS a faixa onde este preço caiu já tem evidência. Antes, homologar
    exigia autorizar o mercado todo — inclusive faixas com CLV negativo."""
    odd, banco, r = _cenario_com_odd([
        _celula("backtest"),
        _celula("homologado", odd_min=1.01, odd_max=99.0),
    ])
    assert r.sinais == 1 and r.candidatos_sombra == 0
    assert len(banco.por_tabela("sinais")) == 1


def test_faixa_fora_do_intervalo_cai_no_geral_e_vira_sombra():
    """Especificidade não é herança: a odd que não pertence à faixa homologada é
    julgada pela regra geral, não pela faixa."""
    odd, banco, r = _cenario_com_odd([
        _celula("backtest"),
        _celula("homologado", odd_min=1.01, odd_max=1.10),   # não cobre a odd do caso
    ])
    assert r.sinais == 0 and r.candidatos_sombra == 1
    assert banco.por_tabela("abortos_l1")[0]["gate_reprovado"] == "mercado_nao_homologado"


def test_faixa_suspensa_nao_vira_nem_sombra():
    """Sombra é calibração AUTORIZADA. Faixa retirada não autoriza nem medir."""
    odd, banco, r = _cenario_com_odd([
        _celula("homologado"),
        _celula("homologado", odd_min=1.01, odd_max=99.0, suspenso_em="2026-07-01T00:00:00Z"),
    ])
    assert r.sinais == 0 and r.candidatos_sombra == 0
    assert banco.por_tabela("abortos_l1")[0]["gate_reprovado"] == "faixa_suspenso"


def test_mercado_com_tudo_suspenso_pula_o_grupo_antes_de_gastar_gate():
    odd, banco, r = _cenario_com_odd([_celula("homologado", suspenso_em="2026-07-01T00:00:00Z")])
    assert r.sinais == 0 and r.candidatos_sombra == 0 and r.nao_autorizados == 1
    assert banco.por_tabela("abortos_l1")[0]["gate_reprovado"] == "mercado_suspenso"


def test_mapa_antigo_continua_significando_qualquer_linha_e_qualquer_odd():
    """Compatibilidade: `{(liga, mercado): status}` É o caso de todos os limites
    nulos. Ler assim mantém o significado — não é tradução."""
    odd, banco, r = _cenario_com_odd({("Premier League", "1x2"): "homologado"})
    assert r.sinais == 1
