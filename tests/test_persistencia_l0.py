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
        self._seq = 0

    def evento_por_id_externo(self, fonte, valor):
        assert fonte == "odds_api"
        return self.eventos.get(valor)

    def casa_por_nome(self, nome):
        return self.casas.get(nome)

    def inserir(self, tabela, registro):
        self._seq += 1
        row = {"id": f"{tabela}-{self._seq}", **registro}
        self.inseridos.append((tabela, row))
        if tabela == "eventos":
            self.eventos[registro["ids_externos"]["odds_api"]] = row
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
