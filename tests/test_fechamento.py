"""Testes do L4 (fechamento/CLV) e do relatório diário."""
import pytest

from sinalizador.comum.erros import e_violacao_unicidade
from sinalizador.l1_gatilhos.devig import devig_shin
from sinalizador.l4_fechamento.clv import (
    clv_pct,
    fechar_evento,
    probs_fechamento_por_mercado,
    prob_implicita,
    rodar_fechamento,
)
from sinalizador.l4_fechamento.relatorio import formatar_relatorio

INICIO = "2026-07-20T21:00:00Z"
REF_1X2 = [("1", 2.0), ("X", 3.5), ("2", 4.0)]


def _p_fech():
    probs, _ = devig_shin([o for _, o in REF_1X2])
    return dict(zip(("1", "X", "2"), probs))


def _snap_ref(sel, odd, ts, *, mercado="1x2", linha=None):
    return {"casa_id": "c-pin", "mercado": mercado, "selecao": sel, "linha": linha,
            "odd": odd, "ts_fonte": ts}


# ---------------- núcleo ----------------

def test_clv_pct_bate_o_fechamento():
    # odd de emissão acima da odd justa de fechamento → CLV positivo
    p = 0.5  # odd justa 2.0
    assert clv_pct(2.10, p) > 0
    assert clv_pct(1.90, p) < 0
    assert abs(clv_pct(2.00, p)) < 1e-9
    assert prob_implicita(2.0) == 0.5


def test_probs_fechamento_usa_ultimo_e_deviga():
    snaps = [
        _snap_ref("1", 1.9, "2026-07-20T19:00:00Z"),   # antigo (será sobrescrito)
        _snap_ref("1", 2.0, "2026-07-20T20:59:00Z"),   # último → fechamento
        _snap_ref("X", 3.5, "2026-07-20T20:59:00Z"),
        _snap_ref("2", 4.0, "2026-07-20T20:59:00Z"),
    ]
    fech = probs_fechamento_por_mercado(snaps)
    p = fech[("1x2", None)]
    assert abs(p["1"] - _p_fech()["1"]) < 1e-9
    assert sum(p.values()) > 0.99  # de-vigado (sem overround)


def test_probs_fechamento_pula_mercado_incompleto():
    snaps = [_snap_ref("1", 2.0, INICIO), _snap_ref("X", 3.5, INICIO)]  # falta "2"
    assert probs_fechamento_por_mercado(snaps) == {}


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

    def casas_ativas(self):
        return [{"id": "c-pin", "nome": "pinnacle", "tipo": "referencia"},
                {"id": "c-b365", "nome": "bet365_br", "tipo": "varejo"}]

    def snapshots_do_evento(self, evento_id, casa_ids=None, ate_iso=None):
        return [s for s in self._snaps if casa_ids is None or s["casa_id"] in casa_ids]

    def sinais_do_evento(self, evento_id, status=None):
        return [s for s in self._sinais if status is None or s["status"] in status]

    def abortos_rastreados_do_evento(self, evento_id):
        return self._abortos

    def clv_ids_registrados(self, evento_id):
        return self._com_clv

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
    return [_snap_ref(sel, odd, INICIO) for sel, odd in REF_1X2]


def test_fechar_evento_sinal_confirmado_gera_clv_real():
    sinal = {"id": "s1", "status": "confirmado", "mercado": "1x2", "selecao": "1",
             "linha": None, "odd_venue": 2.20, "p_justa": _p_fech()["1"]}
    banco = BancoFake(snaps_ref=_snaps_completos(), sinais=[sinal])
    n = fechar_evento(banco, {"id": "ev1", "inicio_utc": INICIO})
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
    fechar_evento(banco, {"id": "ev1", "inicio_utc": INICIO})
    assert banco.clv()[0]["contrafactual"] is True


def test_fechar_evento_aborto_rastreado():
    aborto = {"id": 7, "dossie_parcial": {"mercado": "1x2", "selecao": "1", "linha": None,
                                          "odd_venue": 2.20, "p_justa": _p_fech()["1"]}}
    banco = BancoFake(snaps_ref=_snaps_completos(), abortos=[aborto])
    fechar_evento(banco, {"id": "ev1", "inicio_utc": INICIO})
    clv = banco.clv()[0]
    assert clv["aborto_l1_id"] == 7 and clv["contrafactual"] is True


def test_fechar_evento_nao_duplica_clv():
    sinal = {"id": "s1", "status": "confirmado", "mercado": "1x2", "selecao": "1",
             "linha": None, "odd_venue": 2.2, "p_justa": _p_fech()["1"]}
    banco = BancoFake(snaps_ref=_snaps_completos(), sinais=[sinal], com_clv=({"s1"}, set()))
    assert fechar_evento(banco, {"id": "ev1", "inicio_utc": INICIO}) == 0


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
    n = fechar_evento(banco, {"id": "ev1", "inicio_utc": INICIO})

    assert n == 1                                       # só s2 contou como gravado
    gravados = {r["sinal_id"] for r in banco.clv()}
    assert gravados == {"s2"}                           # s1 não duplicou
    assert banco.encerrados == ["ev1"]                  # o ciclo terminou normalmente


def test_corrida_de_clv_em_aborto_tambem_e_absorvida():
    aborto = {"id": 7, "dossie_parcial": {"mercado": "1x2", "selecao": "1", "linha": None,
                                          "odd_venue": 2.20, "p_justa": _p_fech()["1"]}}
    banco = BancoFake(snaps_ref=_snaps_completos(), abortos=[aborto],
                      clv_ja_no_banco={("a", 7)})
    assert fechar_evento(banco, {"id": "ev1", "inicio_utc": INICIO}) == 0
    assert banco.clv() == []


def test_erro_que_nao_e_unicidade_sobe():
    # Só a violação de unicidade é engolida. Um erro real (rede, coluna faltando)
    # NÃO pode ser confundido com "já existe" — isso esconderia perda de CLV.
    banco = BancoFake(snaps_ref=_snaps_completos(), sinais=[_sinal("s1")],
                      erro_no_insert=RuntimeError("connection reset by peer"))
    with pytest.raises(RuntimeError, match="connection reset"):
        fechar_evento(banco, {"id": "ev1", "inicio_utc": INICIO})


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
    banco = BancoFake(snaps_ref=[_snap_ref("1", 2.0, INICIO)],
                      sinais=[{"id": "s1", "status": "confirmado", "mercado": "1x2",
                               "selecao": "1", "linha": None, "odd_venue": 2.2, "p_justa": 0.5}])
    assert fechar_evento(banco, {"id": "ev1", "inicio_utc": INICIO}) == 0


def test_rodar_fechamento_pulsa_l4():
    banco = BancoFake(snaps_ref=_snaps_completos())
    r = rodar_fechamento(banco, "2026-07-20T23:00:00Z")
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
