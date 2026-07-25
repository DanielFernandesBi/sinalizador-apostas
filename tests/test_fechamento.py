"""Testes do L4 (fechamento/CLV) e do relatório diário."""
import pytest

from sinalizador.comum.tempo import para_datetime
from sinalizador.l1_gatilhos.devig import devig_shin
from sinalizador.l4_fechamento.clv import (
    STATUS_AUDITAVEIS,
    clv_pct,
    prob_implicita,
    processar_evento,
    revisoes_de_fechamento,
    rodar_fechamento,
)
from sinalizador.l4_fechamento.relatorio import formatar_relatorio

INICIO = "2026-07-20T21:00:00Z"
KICKOFF = para_datetime(INICIO)
TS_FECH = "2026-07-20T20:55:00Z"       # revisão de fechamento padrão (300 s antes)
REF_1X2 = [("1", 2.0), ("X", 3.5), ("2", 4.0)]


class GatesFake:
    def __init__(self, fechamento_idade_max_s=600.0, fechamento_assentamento_s=600.0):
        self._v = {"fechamento_idade_max_s": fechamento_idade_max_s,
                   "fechamento_assentamento_s": fechamento_assentamento_s}

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



# ---------------- desfecho terminal por item (achado #4) ----------------


class ErroInfra(RuntimeError):
    """Rede/banco fora — NÃO é desfecho: o item fica pendente."""


class BancoFake:
    """Simula a semântica das funções SQL da 0007 (não é o Postgres — a fidelidade
    da SQL real foi verificada contra o banco, com rollback)."""

    def __init__(self, *, snaps_ref=(), sinais=None, abortos=None, casas=None,
                 desfechos=(), erro_ao_registrar=None):
        self._snaps = list(snaps_ref)
        self._sinais = sinais or []
        self._abortos = abortos or []
        self._casas = casas if casas is not None else [
            {"id": "c-pin", "nome": "pinnacle", "tipo": "referencia"},
            {"id": "c-b365", "nome": "bet365_br", "tipo": "varejo"}]
        # desfechos JÁ existentes no banco (invisíveis ao caminho rápido do app)
        self._desfechos: dict = {k: True for k in desfechos}
        self._erro_ao_registrar = erro_ao_registrar
        self.resultados = []          # linhas de clv_resultados
        self.clv_log = []             # linhas de clv_log
        self.finalizados = []
        self.pulsos = []
        self.timeouts_marcados = 0

    # -- leituras --
    def casas_ativas(self):
        return self._casas

    def snapshots_do_evento(self, evento_id, casa_ids=None, ate_iso=None):
        return [s for s in self._snaps if casa_ids is None or s["casa_id"] in casa_ids]

    def sinais_do_evento(self, evento_id, status=None):
        return [s for s in self._sinais if status is None or s["status"] in status]

    def abortos_rastreados_do_evento(self, evento_id):
        return self._abortos

    def itens_com_desfecho(self, evento_id):
        return ({k[1] for k in self._desfechos if k[0] == "s"},
                {k[1] for k in self._desfechos if k[0] == "a"})

    # -- escritas (espelham as RPCs) --
    def registrar_resultado_clv(self, **c):
        if self._erro_ao_registrar is not None:
            raise self._erro_ao_registrar
        chave = ("s", c["p_sinal_id"]) if c.get("p_sinal_id") else ("a", c["p_aborto_id"])
        if chave in self._desfechos:                     # ux_clv_resultado_*
            return {"registrado": False, "motivo": "ja_registrado"}
        self._desfechos[chave] = True
        clv_id = None
        if c["p_resultado"] == "calculado":              # atômico com o desfecho
            clv_id = len(self.clv_log) + 1
            self.clv_log.append({"id": clv_id, "sinal_id": c.get("p_sinal_id"),
                                 "aborto_l1_id": c.get("p_aborto_id"),
                                 "clv_pct": c.get("p_clv_pct"),
                                 "contrafactual": c.get("p_contrafactual"),
                                 "ts_fechamento": c.get("p_ts_fechamento")})
        self.resultados.append({**c, "clv_log_id": clv_id})
        return {"registrado": True, "clv_log_id": clv_id}

    def finalizar_evento_clv(self, evento_id, detalhe=None):
        # espelha fn_finalizar_evento_clv: recusa se sobrar item sem desfecho
        s_ok, a_ok = self.itens_com_desfecho(evento_id)
        pend = [s for s in self._sinais if s["status"] in STATUS_AUDITAVEIS
                and s["id"] not in s_ok]
        pend += [a for a in self._abortos if a["id"] not in a_ok]
        if pend:
            raise ErroInfra(f"{len(pend)} item(ns) sem desfecho — não finaliza")
        self.finalizados.append(evento_id)
        return {"finalizado": True}

    def fila_fechamento(self, agora_iso, assentamento_s, limite=200):
        return [{"id": "ev1", "inicio_utc": INICIO}]

    def marcar_timeout_crivo(self, agora_iso):
        n = 0
        for s in self._sinais:
            if s["status"] == "aguardando_crivo":
                s["status"] = "timeout_crivo"
                n += 1
        self.timeouts_marcados = n
        return n

    def pulsar(self, daemon, detalhe=None):
        self.pulsos.append((daemon, detalhe))

    # -- helpers de asserção --
    def por_ref(self, sinal_id=None, aborto_id=None):
        for r in self.resultados:
            if sinal_id and r["p_sinal_id"] == sinal_id:
                return r
            if aborto_id and r["p_aborto_id"] == aborto_id:
                return r
        return None


def _snaps_completos():
    return [_snap_ref(sel, odd, TS_FECH) for sel, odd in REF_1X2]


def _sinal(id_, status="confirmado", mercado="1x2", selecao="1", odd=2.20):
    return {"id": id_, "status": status, "mercado": mercado, "selecao": selecao,
            "linha": None, "odd_venue": odd, "p_justa": _p_fech()["1"]}


def _evento():
    return {"id": "ev1", "inicio_utc": INICIO}


def _gates():
    return GatesFake()


# ---- desfecho calculado ----

def test_sinal_confirmado_gera_clv_real_e_desfecho_calculado():
    banco = BancoFake(snaps_ref=_snaps_completos(), sinais=[_sinal("s1")])
    r = processar_evento(banco, _evento(), _gates())

    assert r["calculados"] == 1 and r["indisponiveis"] == 0
    res = banco.por_ref(sinal_id="s1")
    assert res["p_categoria"] == "real" and res["p_resultado"] == "calculado"
    assert banco.clv_log[0]["clv_pct"] > 0          # odd 2.20 > justa (~2.0)
    assert banco.clv_log[0]["contrafactual"] is False
    # ts_fechamento é o da revisão (Sugestão nº 9), não o kickoff
    assert banco.clv_log[0]["ts_fechamento"] == para_datetime(TS_FECH).isoformat()
    assert banco.finalizados == ["ev1"]


def test_vetado_e_contrafactual_l2():
    banco = BancoFake(snaps_ref=_snaps_completos(), sinais=[_sinal("s2", "vetado")])
    processar_evento(banco, _evento(), _gates())
    assert banco.por_ref(sinal_id="s2")["p_categoria"] == "contrafactual_l2"
    assert banco.clv_log[0]["contrafactual"] is True


@pytest.mark.parametrize("status,motivo", [
    ("expirado", "preco_deixou_de_ser_executavel"),
    ("erro", "falha_tecnica_no_crivo"),
    ("timeout_crivo", "crivo_nao_concluido_antes_do_kickoff"),
])
def test_status_operacionais_recebem_clv_em_categoria_propria(status, motivo):
    # Defeito C: antes esses sinais nunca recebiam CLV e o evento encerrava por cima.
    banco = BancoFake(snaps_ref=_snaps_completos(), sinais=[_sinal("s3", status)])
    r = processar_evento(banco, _evento(), _gates())
    res = banco.por_ref(sinal_id="s3")
    assert r["calculados"] == 1
    assert res["p_categoria"] == "contrafactual_operacional"
    assert res["p_motivo"] == motivo
    assert banco.clv_log[0]["contrafactual"] is True


def test_aborto_near_miss_e_candidato_sombra_tem_categorias_distintas():
    dp = {"mercado": "1x2", "selecao": "1", "linha": None, "odd_venue": 2.20,
          "p_justa": _p_fech()["1"]}
    banco = BancoFake(snaps_ref=_snaps_completos(), abortos=[
        {"id": 7, "gate_reprovado": "edge_min_pct", "dossie_parcial": dp},
        {"id": 8, "gate_reprovado": "mercado_nao_homologado", "dossie_parcial": dp},
    ])
    processar_evento(banco, _evento(), _gates())
    assert banco.por_ref(aborto_id=7)["p_categoria"] == "contrafactual_l1"
    assert banco.por_ref(aborto_id=8)["p_categoria"] == "calibracao"


# ---- Defeito A: sucesso parcial não encerra o evento ----

def test_mercado_sem_book_nao_impede_o_outro_e_ambos_tem_desfecho():
    # 1x2 tem book; `ou` não. Antes: evento encerrava e `ou` sumia para sempre.
    banco = BancoFake(
        snaps_ref=_snaps_completos() + [
            _snap_ref("over", 1.9, TS_FECH, mercado="ou")],   # book de `ou` incompleto
        sinais=[_sinal("s1"), _sinal("s2", mercado="ou", selecao="over")])
    r = processar_evento(banco, _evento(), _gates())

    assert r["calculados"] == 1 and r["indisponiveis"] == 1
    assert banco.por_ref(sinal_id="s1")["p_resultado"] == "calculado"
    perdido = banco.por_ref(sinal_id="s2")
    assert perdido["p_resultado"] == "indisponivel_sem_revisao_completa"
    assert perdido["clv_log_id"] is None            # indisponível não gera medição
    assert banco.finalizados == ["ev1"]             # todos os itens têm desfecho


def test_revisao_defasada_vira_desfecho_nomeado_com_idade():
    banco = BancoFake(snaps_ref=_revisao_completa("2026-07-20T20:30:00Z"),  # 1800 s
                      sinais=[_sinal("s1")])
    r = processar_evento(banco, _evento(), _gates())
    res = banco.por_ref(sinal_id="s1")
    assert r["indisponiveis"] == 1
    assert res["p_resultado"] == "indisponivel_revisao_defasada"
    assert res["p_idade_s"] == 1800.0


def test_sem_casa_de_referencia_vira_desfecho_nomeado_nao_retry_eterno():
    # Defeito B: antes retornava sem encerrar e o evento voltava para sempre.
    banco = BancoFake(snaps_ref=_snaps_completos(), sinais=[_sinal("s1")], casas=[])
    r = processar_evento(banco, _evento(), _gates())
    assert r["indisponiveis"] == 1
    assert banco.por_ref(sinal_id="s1")["p_resultado"] == "indisponivel_sem_referencia"
    assert banco.finalizados == ["ev1"]


def test_evento_sem_book_algum_finaliza_com_todos_indisponiveis():
    # Defeito B (o caso puro): nenhum snapshot de referência.
    banco = BancoFake(snaps_ref=[], sinais=[_sinal("s1"), _sinal("s2")])
    r = processar_evento(banco, _evento(), _gates())
    assert r["calculados"] == 0 and r["indisponiveis"] == 2
    assert banco.finalizados == ["ev1"]             # não fica na fila para sempre


# ---- falha de infraestrutura NÃO é desfecho ----

def test_falha_de_infra_deixa_item_pendente_e_nao_finaliza():
    banco = BancoFake(snaps_ref=_snaps_completos(), sinais=[_sinal("s1")],
                      erro_ao_registrar=ErroInfra("connection reset by peer"))
    r = processar_evento(banco, _evento(), _gates())

    assert r["pendentes"] == 1
    assert r["calculados"] == 0 and r["indisponiveis"] == 0
    assert banco.resultados == []                   # NADA gravado como desfecho
    assert banco.finalizados == []                  # evento segue pendente


# ---- idempotência ----

def test_item_com_desfecho_nao_e_reprocessado():
    banco = BancoFake(snaps_ref=_snaps_completos(), sinais=[_sinal("s1")],
                      desfechos=[("s", "s1")])
    r = processar_evento(banco, _evento(), _gates())
    assert r["calculados"] == 0 and banco.resultados == []
    assert banco.finalizados == ["ev1"]             # já estava tudo resolvido


def test_corrida_no_desfecho_nao_duplica():
    # o desfecho já existe no banco, invisível ao caminho rápido: a RPC recusa.
    banco = BancoFake(snaps_ref=_snaps_completos(), sinais=[_sinal("s1")])
    banco._desfechos[("s", "s1")] = True            # gravado por outro processo
    r = processar_evento(banco, _evento(), _gates())
    assert r["calculados"] == 0
    assert banco.clv_log == [] and banco.resultados == []


# ---- ciclo ----

def test_rodar_fechamento_marca_timeout_crivo_e_pulsa():
    banco = BancoFake(snaps_ref=_snaps_completos(),
                      sinais=[_sinal("s1", "aguardando_crivo")])
    r = rodar_fechamento(banco, _gates(), "2026-07-20T23:00:00Z")

    assert r["timeout_crivo"] == 1                  # não sobreviveu ao kickoff
    assert banco.pulsos[-1][0] == "l4"
    # e o sinal convertido recebeu desfecho operacional no mesmo ciclo
    assert banco.por_ref(sinal_id="s1")["p_categoria"] == "contrafactual_operacional"


# ---------------- relatório (Tier 3) ----------------

def _cat(categoria, n, medio=1.0):
    return {"categoria": categoria, "n": n, "clv_medio": medio, "desvio": 3.0}


def test_relatorio_separa_categorias_e_nunca_as_soma():
    # Doutrina §3 (v0.1.7): veto do crivo, near-miss e perda operacional são coisas
    # distintas. Antes viravam uma linha "contrafactual" só.
    acum = [_cat("real", 12, 0.8), _cat("contrafactual_l2", 5, -1.2),
            _cat("contrafactual_l1", 7, 0.3), _cat("contrafactual_operacional", 2, 2.1),
            _cat("calibracao", 40, 0.5)]
    txt = formatar_relatorio(acum, None, [], amostra_minima=200)
    assert "CLV real (confirmados): 0.800% · n=12" in txt
    assert "vetados pelo crivo: -1.200% · n=5" in txt
    assert "near-miss do L1: 0.300% · n=7" in txt
    assert "perdas operacionais: 2.100% · n=2" in txt
    assert "mercados não homologados: 0.500% · n=40" in txt


def test_relatorio_avisa_amostra_pequena_so_no_kpi():
    acum = [_cat("real", 12), _cat("contrafactual_l2", 5)]
    txt = formatar_relatorio(acum, None, [], amostra_minima=200)
    linha_real = [l for l in txt.split("\n") if "CLV real" in l][0]
    linha_veto = [l for l in txt.split("\n") if "vetados pelo crivo" in l][0]
    assert "amostra < 200" in linha_real       # o KPI soberano avisa
    assert "amostra <" not in linha_veto       # as diagnósticas, não


def test_amostra_minima_vem_do_gate_nao_de_constante():
    # regra 6: o número é do gate. Com o gate em 50, uma amostra de 60 não avisa.
    acum = [_cat("real", 60)]
    assert "amostra <" not in formatar_relatorio(acum, None, [], amostra_minima=50)
    assert "amostra < 200" in formatar_relatorio(acum, None, [], amostra_minima=200)
    assert "amostra < 500" in formatar_relatorio(acum, None, [], amostra_minima=500)


def test_relatorio_tem_secao_do_dia_e_acumulado():
    # o relatório dizia "diário" e mostrava só o acumulado desde o início.
    txt = formatar_relatorio([_cat("real", 300, 0.9)], None, [], amostra_minima=200,
                             dia="2026-07-25", clv_do_dia=[_cat("real", 4, -0.5)])
    assert "· 2026-07-25" in txt
    assert "HOJE (2026-07-25, partidas do dia)" in txt and "ACUMULADO" in txt
    assert "n=4" in txt and "n=300" in txt      # o dia não some dentro do acumulado


def test_relatorio_mostra_perda_de_amostra_do_dia():
    perda = [{"dia": "2026-07-25", "mercado": "1x2",
              "motivo": "indisponivel_sem_revisao_completa", "n": 2},
             {"dia": "2026-07-25", "mercado": "ou",
              "motivo": "indisponivel_revisao_defasada", "n": 1}]
    txt = formatar_relatorio([], None, [], amostra_minima=200, dia="2026-07-25",
                             clv_do_dia=[], perda_do_dia=perda)
    assert "Sem CLV: 3" in txt
    assert "sem book completo: 2" in txt and "book defasado: 1" in txt


def test_relatorio_dia_sem_clv_e_explicito():
    txt = formatar_relatorio([], None, [], amostra_minima=200, dia="2026-07-25")
    assert "sem CLV fechado no dia" in txt


def test_relatorio_kill_switch_e_sem_ledger():
    txt = formatar_relatorio([_cat("real", 250, 0.8)],
                             {"saldo": 800, "pico": 1000, "drawdown_pct": 20.0,
                              "kill_switch": True}, [], amostra_minima=200)
    assert "KILL SWITCH" in txt and "amostra <" not in txt
    txt2 = formatar_relatorio([], None, [], amostra_minima=200)
    assert "sem ledger" in txt2 and "sem dados" in txt2


def test_relatorio_lista_daemons_mudos():
    saude = [{"daemon": "l0_referencia", "segundos_em_silencio": 30},
             {"daemon": "l1", "segundos_em_silencio": 7200}]
    txt = formatar_relatorio([], None, saude, amostra_minima=200)
    assert "l1" in txt.split("Daemons mudos:")[1]
    assert "l0_referencia" not in txt.split("Daemons mudos:")[1]
