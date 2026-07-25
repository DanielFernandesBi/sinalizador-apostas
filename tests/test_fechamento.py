"""Testes do L4 (fechamento/CLV) e do relatório diário."""
import pytest

from sinalizador.comum.erros import e_violacao_unicidade
from sinalizador.comum.tempo import para_datetime
from sinalizador.l1_gatilhos.devig import devig_shin
from sinalizador.l4_fechamento.clv import (
    clv_pct,
    fechar_evento,
    prob_implicita,
    revisoes_de_fechamento,
    rodar_fechamento,
)
from sinalizador.l4_fechamento.relatorio import formatar_relatorio

INICIO = "2026-07-20T21:00:00Z"
KICKOFF = para_datetime(INICIO)
TS_FECH = "2026-07-20T20:55:00Z"       # revisão de fechamento padrão (300 s antes)
REF_1X2 = [("1", 2.0), ("X", 3.5), ("2", 4.0)]


class GatesFake:
    def __init__(self, fechamento_idade_max_s=600.0):
        self._v = {"fechamento_idade_max_s": fechamento_idade_max_s}

    def get(self, nome):
        return self._v[nome]


def _p_fech():
    probs, _ = devig_shin([o for _, o in REF_1X2])
    return dict(zip(("1", "X", "2"), probs))


def _snap_ref(sel, odd, ts, *, mercado="1x2", linha=None, casa="c-pin"):
    return {"casa_id": casa, "mercado": mercado, "selecao": sel, "linha": linha,
            "odd": odd, "ts_fonte": ts}


# ---------------- núcleo ----------------

def test_clv_pct_bate_o_fechamento():
    # odd de emissão acima da odd justa de fechamento → CLV positivo
    p = 0.5  # odd justa 2.0
    assert clv_pct(2.10, p) > 0
    assert clv_pct(1.90, p) < 0
    assert abs(clv_pct(2.00, p)) < 1e-9
    assert prob_implicita(2.0) == 0.5


def _revisao_completa(ts, odds=REF_1X2, casa="c-pin", mercado="1x2", linha=None):
    return [_snap_ref(sel, odd, ts, mercado=mercado, linha=linha, casa=casa)
            for sel, odd in odds]


def _fech(snaps, *, inicio=KICKOFF, limite=600.0):
    return revisoes_de_fechamento(snaps, inicio=inicio, limite_idade_s=limite)


def test_revisao_mais_recente_completa_e_escolhida():
    snaps = _revisao_completa("2026-07-20T19:00:00Z") + _revisao_completa("2026-07-20T20:59:00Z")
    fech, ind = _fech(snaps)
    rev = fech[("1x2", None)]
    assert rev.ts_fonte == para_datetime("2026-07-20T20:59:00Z")
    assert abs(rev.probs["1"] - _p_fech()["1"]) < 1e-9
    assert sum(rev.probs.values()) > 0.99   # de-vigado (sem overround)
    assert ind == []


def test_revisao_mais_recente_incompleta_nao_e_misturada_com_a_anterior():
    # O CORAÇÃO do achado #3: às 20h59 a seleção "2" foi suspensa. O fechamento tem
    # de ser o book INTEIRO das 20h55 — jamais 1 e X de 20h59 casados com 2 de 20h55.
    snaps = _revisao_completa("2026-07-20T20:55:00Z") + [
        _snap_ref("1", 1.80, "2026-07-20T20:59:00Z"),   # revisão parcial (sem "2")
        _snap_ref("X", 3.60, "2026-07-20T20:59:00Z"),
    ]
    fech, ind = _fech(snaps)
    rev = fech[("1x2", None)]
    assert rev.ts_fonte == para_datetime("2026-07-20T20:55:00Z")   # a completa
    assert rev.idade_s == 300.0
    # as probabilidades vêm SÓ das odds das 20h55 (1.80/3.60 não entraram)
    assert abs(rev.probs["1"] - _p_fech()["1"]) < 1e-9


def test_revisao_completa_anterior_dentro_do_gate_e_usada():
    snaps = _revisao_completa("2026-07-20T20:55:00Z")     # 300 s antes do kickoff
    fech, ind = _fech(snaps, limite=600.0)
    assert ("1x2", None) in fech and ind == []


def test_revisao_completa_anterior_fora_do_gate_e_rejeitada():
    snaps = _revisao_completa("2026-07-20T20:30:00Z")     # 1800 s antes
    fech, ind = _fech(snaps, limite=600.0)
    assert fech == {}
    assert len(ind) == 1
    assert ind[0].motivo == "revisao_completa_defasada"
    assert ind[0].idade_s == 1800.0 and ind[0].limite_s == 600.0


def test_limite_exato_do_gate_e_aceito():
    snaps = _revisao_completa("2026-07-20T20:50:00Z")     # exatamente 600 s
    fech, ind = _fech(snaps, limite=600.0)
    assert ("1x2", None) in fech and ind == []            # <= limite, não <


def test_snapshot_posterior_ao_kickoff_nunca_e_usado():
    snaps = _revisao_completa("2026-07-20T20:55:00Z") + _revisao_completa("2026-07-20T21:05:00Z")
    fech, _ = _fech(snaps)
    assert fech[("1x2", None)].ts_fonte == para_datetime("2026-07-20T20:55:00Z")


def test_so_revisao_posterior_ao_kickoff_nao_gera_fechamento():
    snaps = _revisao_completa("2026-07-20T21:05:00Z")
    fech, ind = _fech(snaps)
    assert fech == {} and ind[0].motivo == "sem_revisao_completa"


def test_books_de_casas_de_referencia_diferentes_nunca_sao_combinados():
    # duas referências, cada uma com meio book na mesma revisão: nenhuma fecha.
    snaps = [
        _snap_ref("1", 2.0, "2026-07-20T20:55:00Z", casa="c-pin"),
        _snap_ref("X", 3.5, "2026-07-20T20:55:00Z", casa="c-pin"),
        _snap_ref("2", 4.0, "2026-07-20T20:55:00Z", casa="c-pin2"),   # outra referência
    ]
    fech, ind = _fech(snaps)
    assert fech == {}
    assert ind[0].motivo == "sem_revisao_completa"


def test_mercado_incompleto_nao_produz_shin():
    snaps = [_snap_ref("1", 2.0, "2026-07-20T20:55:00Z"),
             _snap_ref("X", 3.5, "2026-07-20T20:55:00Z")]        # falta "2"
    fech, ind = _fech(snaps)
    assert fech == {} and ind[0].motivo == "sem_revisao_completa"


def test_capturas_repetidas_da_mesma_revisao_nao_alteram_probabilidades():
    # o L0 recaptura a MESMA revisão (mesmo ts_fonte) em ciclos diferentes.
    uma = _revisao_completa("2026-07-20T20:55:00Z")
    fech_1, _ = _fech(uma)
    fech_n, _ = _fech(uma + uma + uma)
    assert fech_n[("1x2", None)].probs == fech_1[("1x2", None)].probs
    assert fech_n[("1x2", None)].ts_fonte == fech_1[("1x2", None)].ts_fonte


def test_ah_usa_a_linha_canonica():
    # achado 5 + #3: o AH fecha por (mercado, linha canônica) — mandante e visitante
    # na MESMA linha, na mesma revisão.
    snaps = [
        _snap_ref("mandante", 1.90, "2026-07-20T20:55:00Z", mercado="ah", linha=-0.5),
        _snap_ref("visitante", 1.95, "2026-07-20T20:55:00Z", mercado="ah", linha=-0.5),
    ]
    fech, ind = _fech(snaps)
    assert ("ah", -0.5) in fech and ind == []
    assert set(fech[("ah", -0.5)].probs) == {"mandante", "visitante"}


def test_mercado_fora_de_escopo_nao_vira_indisponibilidade():
    # fora do escopo ≠ dado faltando: não polui o registro de perda de amostra.
    snaps = [_snap_ref("sim", 2.0, "2026-07-20T20:55:00Z", mercado="ambas_marcam")]
    fech, ind = _fech(snaps)
    assert fech == {} and ind == []


class ErroUnicidade(RuntimeError):
    """Equivale ao 23505 que o banco levanta em ux_clv_sinal / ux_clv_aborto."""


class BancoFake:
    def __init__(self, *, snaps_ref, sinais=None, abortos=None, com_clv=(set(), set()),
                 clv_ja_no_banco=(), erro_no_insert=None):
        self._snaps = snaps_ref
        self._sinais = sinais or []
        self._abortos = abortos or []
        self._com_clv = com_clv
        # achado #2: linhas que JÁ existem no banco mas que o dedup em código NÃO viu
        # (a corrida: outro processo gravou entre a leitura e o INSERT).
        self._clv_ja_no_banco = set(clv_ja_no_banco)
        self._erro_no_insert = erro_no_insert
        self.inseridos = []
        self.encerrados = []
        self.pulsos = []
        self.consultas_clv = []   # (sinal_ids, aborto_ids) perguntados por evento

    def casas_ativas(self):
        return [{"id": "c-pin", "nome": "pinnacle", "tipo": "referencia"},
                {"id": "c-b365", "nome": "bet365_br", "tipo": "varejo"}]

    def snapshots_do_evento(self, evento_id, casa_ids=None, ate_iso=None):
        return [s for s in self._snaps if casa_ids is None or s["casa_id"] in casa_ids]

    def sinais_do_evento(self, evento_id, status=None):
        return [s for s in self._sinais if status is None or s["status"] in status]

    def abortos_rastreados_do_evento(self, evento_id):
        return self._abortos

    def clv_ids_registrados(self, *, sinal_ids=(), aborto_ids=()):
        # achado #2.1: a consulta é FILTRADA pelos ids do evento em fechamento.
        # Guarda o que foi perguntado para os testes de isolamento e devolve só a
        # interseção — é o que o `in_` faria no banco.
        self.consultas_clv.append((list(sinal_ids), list(aborto_ids)))
        com_s, com_a = self._com_clv
        return (com_s & set(sinal_ids)), (com_a & set(aborto_ids))

    def inserir(self, tabela, registro):
        if tabela == "clv_log":
            if self._erro_no_insert is not None:
                raise self._erro_no_insert
            # simula ux_clv_sinal / ux_clv_aborto (migration 0005)
            chave = ("s", registro["sinal_id"]) if registro.get("sinal_id") \
                else ("a", registro["aborto_l1_id"])
            if chave in self._clv_ja_no_banco:
                raise ErroUnicidade(
                    'duplicate key value violates unique constraint "ux_clv_sinal"')
            self._clv_ja_no_banco.add(chave)
        row = {"id": len(self.inseridos) + 1, **registro}
        self.inseridos.append((tabela, row))
        return row

    def marcar_evento_encerrado(self, evento_id):
        self.encerrados.append(evento_id)

    def eventos_iniciados_sem_status_final(self, ate_iso, limite=200):
        return [{"id": "ev1", "inicio_utc": INICIO}]

    def pulsar(self, daemon, detalhe=None):
        self.pulsos.append((daemon, detalhe))

    def clv(self):
        return [r for (t, r) in self.inseridos if t == "clv_log"]


def _snaps_completos():
    return [_snap_ref(sel, odd, TS_FECH) for sel, odd in REF_1X2]


def test_fechar_evento_sinal_confirmado_gera_clv_real():
    sinal = {"id": "s1", "status": "confirmado", "mercado": "1x2", "selecao": "1",
             "linha": None, "odd_venue": 2.20, "p_justa": _p_fech()["1"]}
    banco = BancoFake(snaps_ref=_snaps_completos(), sinais=[sinal])
    n = fechar_evento(banco, {"id": "ev1", "inicio_utc": INICIO}, GatesFake())
    assert n == 1
    clv = banco.clv()[0]
    assert clv["sinal_id"] == "s1" and clv["contrafactual"] is False
    # odd_venue 2.20 > odd justa (~2.0) → CLV positivo
    assert clv["clv_pct"] > 0
    assert banco.encerrados == ["ev1"]


def test_fechar_evento_vetado_e_contrafactual():
    sinal = {"id": "s2", "status": "vetado", "mercado": "1x2", "selecao": "1",
             "linha": None, "odd_venue": 2.20, "p_justa": _p_fech()["1"]}
    banco = BancoFake(snaps_ref=_snaps_completos(), sinais=[sinal])
    fechar_evento(banco, {"id": "ev1", "inicio_utc": INICIO}, GatesFake())
    assert banco.clv()[0]["contrafactual"] is True


def test_fechar_evento_aborto_rastreado():
    aborto = {"id": 7, "dossie_parcial": {"mercado": "1x2", "selecao": "1", "linha": None,
                                          "odd_venue": 2.20, "p_justa": _p_fech()["1"]}}
    banco = BancoFake(snaps_ref=_snaps_completos(), abortos=[aborto])
    fechar_evento(banco, {"id": "ev1", "inicio_utc": INICIO}, GatesFake())
    clv = banco.clv()[0]
    assert clv["aborto_l1_id"] == 7 and clv["contrafactual"] is True


def test_fechar_evento_nao_duplica_clv():
    sinal = {"id": "s1", "status": "confirmado", "mercado": "1x2", "selecao": "1",
             "linha": None, "odd_venue": 2.2, "p_justa": _p_fech()["1"]}
    banco = BancoFake(snaps_ref=_snaps_completos(), sinais=[sinal], com_clv=({"s1"}, set()))
    assert fechar_evento(banco, {"id": "ev1", "inicio_utc": INICIO}, GatesFake()) == 0


# ---- achado #2: idempotência do CLV garantida pelo banco ----

def _sinal(id_, odd=2.2):
    return {"id": id_, "status": "confirmado", "mercado": "1x2", "selecao": "1",
            "linha": None, "odd_venue": odd, "p_justa": _p_fech()["1"]}


def test_corrida_de_clv_nao_duplica_nem_derruba_o_ciclo():
    # O dedup em código NÃO viu s1 (outro processo gravou entre a leitura e o INSERT):
    # o banco recusa por ux_clv_sinal. O fechamento tem de ABSORVER a recusa e seguir
    # gravando os demais — perder o CLV de s2 por causa da duplicata de s1 seria
    # perder amostra do KPI soberano.
    banco = BancoFake(snaps_ref=_snaps_completos(),
                      sinais=[_sinal("s1"), _sinal("s2")],
                      clv_ja_no_banco={("s", "s1")})   # já existe no banco, invisível ao dedup
    n = fechar_evento(banco, {"id": "ev1", "inicio_utc": INICIO}, GatesFake())

    assert n == 1                                       # só s2 contou como gravado
    gravados = {r["sinal_id"] for r in banco.clv()}
    assert gravados == {"s2"}                           # s1 não duplicou
    assert banco.encerrados == ["ev1"]                  # o ciclo terminou normalmente


def test_corrida_de_clv_em_aborto_tambem_e_absorvida():
    aborto = {"id": 7, "dossie_parcial": {"mercado": "1x2", "selecao": "1", "linha": None,
                                          "odd_venue": 2.20, "p_justa": _p_fech()["1"]}}
    banco = BancoFake(snaps_ref=_snaps_completos(), abortos=[aborto],
                      clv_ja_no_banco={("a", 7)})
    assert fechar_evento(banco, {"id": "ev1", "inicio_utc": INICIO}, GatesFake()) == 0
    assert banco.clv() == []


def test_consulta_de_clv_e_filtrada_pelos_ids_do_evento():
    # achado #2.1: pergunta SÓ pelos ids deste evento — não varre clv_log inteira.
    aborto = {"id": 7, "dossie_parcial": {"mercado": "1x2", "selecao": "1", "linha": None,
                                          "odd_venue": 2.20, "p_justa": _p_fech()["1"]}}
    banco = BancoFake(snaps_ref=_snaps_completos(),
                      sinais=[_sinal("s1"), _sinal("s2")], abortos=[aborto])
    fechar_evento(banco, {"id": "ev1", "inicio_utc": INICIO}, GatesFake())

    assert banco.consultas_clv == [(["s1", "s2"], [7])]   # uma consulta, só estes ids


def test_evento_sem_sinais_nem_abortos_nao_consulta_clv():
    # lista vazia não vira consulta (nem ida à rede) — nem para sinais, nem para abortos.
    banco = BancoFake(snaps_ref=_snaps_completos())
    fechar_evento(banco, {"id": "ev1", "inicio_utc": INICIO}, GatesFake())
    assert banco.consultas_clv == [([], [])]
    assert banco.clv() == []


def test_clv_de_outro_evento_nao_suprime_este():
    # ISOLAMENTO: um CLV já registrado para o sinal de OUTRO evento não pode fazer o
    # fechamento deste pular o seu próprio sinal. Com a varredura global antiga isso
    # era "seguro por acidente" (o conjunto era grande demais); agora é por construção.
    banco = BancoFake(snaps_ref=_snaps_completos(), sinais=[_sinal("s-deste-evento")],
                      com_clv=({"s-de-outro-evento"}, {999}))
    n = fechar_evento(banco, {"id": "ev1", "inicio_utc": INICIO}, GatesFake())

    assert n == 1
    assert {r["sinal_id"] for r in banco.clv()} == {"s-deste-evento"}
    assert banco.consultas_clv == [(["s-deste-evento"], [])]  # nem perguntou pelo alheio


class _ClienteConsultaFake:
    """Grava a cadeia de chamadas do supabase-py para inspecionar a consulta montada."""

    def __init__(self, linhas_por_coluna):
        self._linhas = linhas_por_coluna     # {'sinal_id': [...], 'aborto_l1_id': [...]}
        self.consultas = []                  # (tabela, coluna, valores)
        self._atual = None

    def table(self, tabela):
        self._tabela = tabela
        return self

    def select(self, colunas):
        self._coluna = colunas
        return self

    def in_(self, coluna, valores):
        self._atual = (self._tabela, coluna, list(valores))
        return self

    def execute(self):
        assert self._atual is not None, "consulta sem filtro in_ — varreria a tabela"
        tabela, coluna, valores = self._atual
        self.consultas.append((tabela, coluna, valores))
        self._atual = None
        return type("R", (), {"data": [{coluna: v} for v in self._linhas.get(coluna, [])
                                       if v in valores]})()


def test_banco_clv_ids_registrados_consulta_filtrada():
    # A consulta REAL (não o fake do fechamento): filtra por `in_` nos dois lados e
    # devolve só o que existe. Antes lia `clv_log` inteira, ignorando o parâmetro.
    from sinalizador.comum.db import Banco

    cli = _ClienteConsultaFake({"sinal_id": ["s1", "s9"], "aborto_l1_id": [7]})
    banco = Banco(client=cli)
    com_s, com_a = banco.clv_ids_registrados(sinal_ids=["s1", "s2"], aborto_ids=[7, 8])

    assert com_s == {"s1"} and com_a == {7}
    assert cli.consultas == [
        ("clv_log", "sinal_id", ["s1", "s2"]),
        ("clv_log", "aborto_l1_id", [7, 8]),
    ]


def test_banco_clv_ids_registrados_nao_consulta_com_lista_vazia():
    # Lista vazia não vira consulta — nem ida à rede. (O fake levantaria se `execute`
    # fosse chamado sem `in_`.)
    from sinalizador.comum.db import Banco

    cli = _ClienteConsultaFake({})
    banco = Banco(client=cli)
    assert banco.clv_ids_registrados(sinal_ids=[], aborto_ids=[]) == (set(), set())
    assert banco.clv_ids_registrados() == (set(), set())
    assert cli.consultas == []


def test_erro_que_nao_e_unicidade_sobe():
    # Só a violação de unicidade é engolida. Um erro real (rede, coluna faltando)
    # NÃO pode ser confundido com "já existe" — isso esconderia perda de CLV.
    banco = BancoFake(snaps_ref=_snaps_completos(), sinais=[_sinal("s1")],
                      erro_no_insert=RuntimeError("connection reset by peer"))
    with pytest.raises(RuntimeError, match="connection reset"):
        fechar_evento(banco, {"id": "ev1", "inicio_utc": INICIO}, GatesFake())


def test_reconhece_violacao_de_unicidade():
    assert e_violacao_unicidade(ErroUnicidade(
        'duplicate key value violates unique constraint "ux_clv_sinal"'))
    assert e_violacao_unicidade(RuntimeError("23505"))
    # embrulhado (o supabase-py re-levanta o erro original como causa)
    interno = ErroUnicidade("23505")
    externo = RuntimeError("falha ao inserir")
    externo.__cause__ = interno
    assert e_violacao_unicidade(externo)
    # não confunde outros erros
    assert not e_violacao_unicidade(RuntimeError("connection reset by peer"))
    assert not e_violacao_unicidade(ValueError("odd inválida"))


def test_fechar_evento_sem_book_completo_nao_gera_clv():
    # referência só com "1" → sem de-vig → sem CLV, mas encerra o evento
    banco = BancoFake(snaps_ref=[_snap_ref("1", 2.0, TS_FECH)],
                      sinais=[{"id": "s1", "status": "confirmado", "mercado": "1x2",
                               "selecao": "1", "linha": None, "odd_venue": 2.2, "p_justa": 0.5}])
    assert fechar_evento(banco, {"id": "ev1", "inicio_utc": INICIO}, GatesFake()) == 0


def test_rodar_fechamento_pulsa_l4():
    banco = BancoFake(snaps_ref=_snaps_completos())
    r = rodar_fechamento(banco, GatesFake(), "2026-07-20T23:00:00Z")
    assert r["eventos"] == 1
    assert banco.pulsos and banco.pulsos[-1][0] == "l4"


# ---------------- relatório ----------------

def test_relatorio_avisa_amostra_pequena():
    clv = [{"contrafactual": False, "n": 12, "clv_medio": 1.3, "desvio": 4.0},
           {"contrafactual": True, "n": 30, "clv_medio": -0.5, "desvio": 3.0}]
    banca = {"saldo": 980, "pico": 1000, "drawdown_pct": 2.0, "kill_switch": False}
    saude = [{"daemon": "l0_referencia", "segundos_em_silencio": 30},
             {"daemon": "l1", "segundos_em_silencio": 7200}]
    txt = formatar_relatorio(clv, banca, saude)
    assert "CLV real" in txt and "amostra < 200" in txt
    assert "contrafactual" in txt
    assert "l1" in txt and "l0_referencia" not in txt.split("Daemons mudos:")[1]


def test_relatorio_kill_switch_e_sem_ledger():
    clv = [{"contrafactual": False, "n": 250, "clv_medio": 0.8, "desvio": 3.0}]
    txt = formatar_relatorio(clv, {"saldo": 800, "pico": 1000, "drawdown_pct": 20.0, "kill_switch": True}, [])
    assert "KILL SWITCH" in txt and "amostra < 200" not in txt
    txt2 = formatar_relatorio([], None, [])
    assert "sem ledger" in txt2 and "sem dados" in txt2
