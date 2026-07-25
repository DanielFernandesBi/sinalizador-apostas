"""Testes da persistência do L0 (E1): get-or-create + snapshot com ts_fonte."""
from sinalizador.l0_captura.persistencia import (
    garantir_casa,
    garantir_evento,
    gravar_snapshot,
)


class BancoFake:
    def __init__(self, eventos=None, casas=None):
        self.eventos = eventos or {}   # id_api -> row
        self.casas = casas or {}       # nome -> row
        self.inseridos = []
        self.revisoes = []
        self._seq = 0

    def garantir_evento(self, dados):
        """Espelha `fn_garantir_evento` (0016): identidade única por id externo,
        atualização dos fatos que a fonte governa e revisão registrada."""
        id_api = (dados.get("ids_externos") or {}).get("odds_api")
        if not id_api:
            return {"id": None, "criado": False, "motivo": "sem_id_da_fonte"}
        if not dados.get("inicio_utc"):
            return {"id": None, "criado": False, "motivo": "sem_inicio_utc"}
        atual = self.eventos.get(id_api)
        if atual is None:
            self._seq += 1
            row = {"id": f"eventos-{self._seq}", **dados}
            self.eventos[id_api] = row
            self.inseridos.append(("eventos", row))
            return {"id": row["id"], "criado": True, "alterado": False}
        campos = [c for c in ("inicio_utc", "mandante", "visitante", "liga")
                  if dados.get(c) is not None and atual.get(c) != dados.get(c)]
        if not campos:
            return {"id": atual["id"], "criado": False, "alterado": False}
        tipo = "remarcado" if "inicio_utc" in campos else "corrigido"
        atual.update({c: dados[c] for c in campos})
        if tipo == "remarcado":
            atual["invalidado_em"] = "2026-07-20T18:00:00Z"
        self.revisoes.append({"evento_id": atual["id"], "tipo": tipo, "campos": campos})
        return {"id": atual["id"], "criado": False, "alterado": True,
                "tipo": tipo, "campos": campos}

    def casa_por_nome(self, nome):
        return self.casas.get(nome)

    def inserir(self, tabela, registro):
        self._seq += 1
        row = {"id": f"{tabela}-{self._seq}", **registro}
        self.inseridos.append((tabela, row))
        if tabela == "casas":
            self.casas[registro["nome"]] = row
        return row


def _ev_norm(id_api="ev1"):
    return {"esporte": "futebol", "liga": "Premier League", "mandante": "A",
            "visitante": "B", "inicio_utc": "2026-07-20T19:00:00Z",
            "ids_externos": {"odds_api": id_api}}


def test_garantir_evento_cria_quando_ausente():
    banco = BancoFake()
    eid = garantir_evento(banco, _ev_norm())
    assert eid == "eventos-1"
    assert banco.inseridos[0][0] == "eventos"


def test_garantir_evento_reusa_existente():
    banco = BancoFake(eventos={"ev1": {"id": "existe-1"}})
    eid = garantir_evento(banco, _ev_norm())
    assert eid == "existe-1"
    assert banco.inseridos == []   # não inseriu de novo


def test_garantir_evento_sem_id_da_fonte_retorna_none():
    banco = BancoFake()
    ev = _ev_norm()
    ev["ids_externos"] = {}
    assert garantir_evento(banco, ev) is None
    assert banco.inseridos == []


def test_garantir_casa_cria_varejo_e_cacheia():
    banco = BancoFake()
    cache = {}
    cid = garantir_casa(banco, "bet365_br", tipo="varejo", cache=cache)
    assert cid == "casas-1" and cache["bet365_br"] == "casas-1"
    # segunda chamada usa o cache, sem novo INSERT
    garantir_casa(banco, "bet365_br", tipo="varejo", cache=cache)
    assert len(banco.inseridos) == 1
    assert banco.inseridos[0][1]["tipo"] == "varejo"


def test_garantir_casa_reusa_seedada():
    banco = BancoFake(casas={"pinnacle": {"id": "casa-pinn"}})
    cid = garantir_casa(banco, "pinnacle", tipo="referencia", cache={})
    assert cid == "casa-pinn"
    assert banco.inseridos == []


def test_gravar_snapshot_usa_ts_fonte_da_api():
    banco = BancoFake()
    snap = {"casa": "pinnacle", "mercado": "1x2", "selecao": "1", "linha": None,
            "odd": 2.10, "ts_fonte": "2026-07-20T18:31:00Z", "raw": {"x": 1}}
    row = gravar_snapshot(banco, evento_id="ev-1", casa_id="casa-1", snap=snap)
    tabela, reg = banco.inseridos[0]
    assert tabela == "odds_snapshots"
    assert reg["ts_fonte"] == "2026-07-20T18:31:00Z"  # da fonte, nunca relógio local
    assert reg["evento_id"] == "ev-1" and reg["casa_id"] == "casa-1"
    assert reg["odd"] == 2.10 and reg["linha"] is None
    assert "ts_captura" not in reg   # deixado para o default do schema (now())
    assert row["id"] == "odds_snapshots-1"


# ---------------- a fonte governa os fatos do evento (P1.2) ----------------

def test_kickoff_remarcado_atualiza_o_banco_e_registra_revisao():
    """Antes, o evento existente NUNCA era atualizado: o kickoff velho ficava para
    sempre e corrompia de uma vez a trava de apito do L1, a fila do L4 e a linha de
    fechamento."""
    banco = BancoFake()
    eid = garantir_evento(banco, _ev_norm())

    novo = {**_ev_norm(), "inicio_utc": "2026-07-20T22:30:00Z"}
    assert garantir_evento(banco, novo) == eid          # mesma partida, mesmo id
    assert banco.eventos["ev1"]["inicio_utc"] == "2026-07-20T22:30:00Z"
    assert banco.revisoes == [{"evento_id": eid, "tipo": "remarcado",
                               "campos": ["inicio_utc"]}]


def test_correcao_de_nome_nao_e_remarcacao():
    banco = BancoFake()
    garantir_evento(banco, _ev_norm())
    garantir_evento(banco, {**_ev_norm(), "mandante": "A FC"})

    assert banco.revisoes[0]["tipo"] == "corrigido"
    assert banco.eventos["ev1"]["mandante"] == "A FC"


def test_payload_identico_nao_gera_revisao():
    banco = BancoFake()
    garantir_evento(banco, _ev_norm())
    garantir_evento(banco, _ev_norm())
    assert banco.revisoes == []
    assert len([t for t, _ in banco.inseridos if t == "eventos"]) == 1


def test_evento_sem_inicio_e_descartado():
    """Sem horário não se cria evento: `inicio_utc` é a pedra de tudo o que vem
    depois, e chutá-lo seria pior que perder o tick (P6)."""
    banco = BancoFake()
    sem_inicio = {k: v for k, v in _ev_norm().items() if k != "inicio_utc"}
    assert garantir_evento(banco, sem_inicio) is None
    assert banco.eventos == {}
