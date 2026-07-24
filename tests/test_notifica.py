"""Testes do L3 (notificação): cartão, re-checagem de preço, expiração, alertas.

Tudo com fakes: nem rede, nem token, nem Supabase. O núcleo depende só do Protocol
`Bot` e da fachada do `Banco`.
"""
from sinalizador.l3_notifica.bot import BotTelegram
from sinalizador.l3_notifica.cartao import formatar_cartao, janela_fechou, odd_atual, preco_caiu
from sinalizador.l3_notifica.notifica import (
    ResumoL3,
    alerta_drawdown,
    emitir_confirmados,
    entregar_pendentes,
    expirar_pendentes,
    processar,
)

ODD_MIN = 1.90


def _sinal(id="s1", *, status="confirmado", odd_venue=2.05, odd_min=ODD_MIN, linha=None):
    return {
        "id": id, "evento_id": "ev1", "casa_venue_id": "c-b365", "status": status,
        "mercado": "1x2", "selecao": "1", "linha": linha, "odd_venue": odd_venue,
        "edge_liquido_pct": 3.1, "stake_pct": 1.2, "odd_minima_aceitavel": odd_min,
        "gatilho": "value_bet", "gatilho_anomalo": False,
        "dossie": {"evento": {"liga": "Premier League", "partida": "A x B", "casa_venue": "bet365_br"},
                   "liquidez": {"sombra_varejo": True}, "banca_origem": "papel"},
    }


def _snap(odd):
    return {"odd": odd, "ts_captura": "2026-07-21T12:00:00Z"}


class BotFake:
    def __init__(self, ok=True):
        self.ok = ok
        self.enviados = []

    def enviar(self, texto):
        self.enviados.append(texto)
        return self.ok


class BancoFake:
    def __init__(self, *, confirmados=None, aguardando=None, snaps=None, crivos=None,
                 eventos=None, notif_por_sinal=None, pendentes=None, banca=None):
        self._confirmados = confirmados or []
        self._aguardando = aguardando or []
        self._snaps = snaps or {}          # (evento,casa,mercado,sel,linha) -> snap
        self._crivos = crivos or {}
        self._eventos = eventos or {}
        self._notif_por_sinal = notif_por_sinal or {}  # sinal_id -> [tipos já existentes]
        self._pendentes = list(pendentes or [])
        self._banca = banca
        self.inseridos = []
        self.transicoes = []
        self.entregues = []
        self.pulsos = []

    def sinais_por_status(self, status, limite=200):
        return self._confirmados if status == "confirmado" else []

    def sinais_aguardando_crivo(self, limite=200):
        return self._aguardando

    def ultimo_snapshot_venue(self, evento_id, casa_id, mercado, selecao, linha):
        return self._snaps.get((evento_id, casa_id, mercado, selecao, linha))

    def crivo_do_sinal(self, sinal_id):
        return self._crivos.get(sinal_id)

    def evento_por_id(self, evento_id):
        return self._eventos.get(evento_id)

    def notificacoes_do_sinal(self, sinal_id, tipo=None):
        tipos = self._notif_por_sinal.get(sinal_id, [])
        return [{"tipo": t} for t in tipos if tipo is None or t == tipo]

    def notificacoes_pendentes(self, limite=200):
        return self._pendentes

    def banca_atual(self):
        return self._banca

    def inserir(self, tabela, registro):
        row = {"id": len(self.inseridos) + 1, **registro}
        self.inseridos.append((tabela, row))
        if tabela == "notificacoes" and not registro.get("entregue", False):
            self._pendentes.append(row)
        return row

    def transicionar_status_sinal(self, sinal_id, novo_status):
        self.transicoes.append((sinal_id, novo_status))
        return {"id": sinal_id, "status": novo_status}

    def marcar_notificacao_entregue(self, notif_id):
        self.entregues.append(notif_id)
        return {"id": notif_id, "entregue": True}

    def pulsar(self, daemon, detalhe=None):
        self.pulsos.append((daemon, detalhe))

    def notificacoes(self, tipo=None):
        return [r for (t, r) in self.inseridos if t == "notificacoes" and (tipo is None or r["tipo"] == tipo)]


# ---------------- cartão + re-checagem de preço ----------------

def test_odd_atual_e_janela():
    assert odd_atual(_snap(2.0)) == 2.0
    assert odd_atual(None) is None
    assert odd_atual({"odd": None}) is None
    assert janela_fechou(1.85, 1.90) is True       # abaixo da mínima
    assert janela_fechou(None, 1.90) is True        # sem preço → fail-safe (não envia)
    assert janela_fechou(1.95, 1.90) is False
    assert preco_caiu(1.85, 1.90) is True
    assert preco_caiu(None, 1.90) is False          # ausência não expira


def test_formatar_cartao_tem_o_essencial():
    txt = formatar_cartao(_sinal(), {"verdict": "CONFIRMA", "observacao": "linha estável"},
                          {"liga": "Premier League", "mandante": "A", "visitante": "B"},
                          odd_atual_venue=2.05)
    assert "SINAL CONFIRMADO" in txt and "SOMBRA" in txt and "banca de papel" in txt
    assert "MÍNIMA aceitável: 1.900" in txt
    assert "Edge líquido: 3.10%" in txt and "Stake: 1.20%" in txt
    assert "linha estável" in txt


# ---------------- emissão dos confirmados ----------------

def test_confirmado_com_preco_ok_envia_e_registra():
    s = _sinal()
    banco = BancoFake(confirmados=[s], snaps={("ev1", "c-b365", "1x2", "1", None): _snap(2.05)},
                      crivos={"s1": {"verdict": "CONFIRMA", "observacao": None}})
    bot = BotFake()
    r = emitir_confirmados(banco, bot)
    assert r.enviados == 1 and r.suprimidos == 0
    assert len(bot.enviados) == 1
    notas = banco.notificacoes(tipo="sinal")
    assert len(notas) == 1 and notas[0]["entregue"] is True


def test_confirmado_com_janela_fechada_suprime_sem_enviar():
    s = _sinal(odd_venue=2.05)
    # preço atual 1.80 < mínima 1.90 → janela fechou
    banco = BancoFake(confirmados=[s], snaps={("ev1", "c-b365", "1x2", "1", None): _snap(1.80)})
    bot = BotFake()
    r = emitir_confirmados(banco, bot)
    assert r.enviados == 0 and r.suprimidos == 1
    assert bot.enviados == []                      # NADA enviado (E4.2)
    adm = banco.notificacoes(tipo="administrativo")
    assert len(adm) == 1 and adm[0]["entregue"] is True and "expirado-no-envio" in adm[0]["conteudo"]


def test_confirmado_sem_preco_fresco_nao_envia():
    s = _sinal()
    banco = BancoFake(confirmados=[s], snaps={})   # sem snapshot → fail-safe
    bot = BotFake()
    r = emitir_confirmados(banco, bot)
    assert r.enviados == 0 and r.suprimidos == 1 and bot.enviados == []


def test_confirmado_ja_notificado_nao_reenvia():
    s = _sinal()
    banco = BancoFake(confirmados=[s], snaps={("ev1", "c-b365", "1x2", "1", None): _snap(2.05)},
                      notif_por_sinal={"s1": ["sinal"]})
    bot = BotFake()
    r = emitir_confirmados(banco, bot)
    assert r.enviados == 0 and bot.enviados == []


def test_envio_falho_deixa_nota_pendente():
    s = _sinal()
    banco = BancoFake(confirmados=[s], snaps={("ev1", "c-b365", "1x2", "1", None): _snap(2.05)})
    bot = BotFake(ok=False)
    emitir_confirmados(banco, bot)
    nota = banco.notificacoes(tipo="sinal")[0]
    assert nota["entregue"] is False               # não entregue → retry no próximo ciclo


# ---------------- expiração de aguardando_crivo (frescor) ----------------

def test_expira_aguardando_crivo_com_preco_caido():
    s = _sinal(id="s2", status="aguardando_crivo")
    banco = BancoFake(aguardando=[s], snaps={("ev1", "c-b365", "1x2", "1", None): _snap(1.80)})
    n = expirar_pendentes(banco)
    assert n == 1 and banco.transicoes == [("s2", "expirado")]


def test_nao_expira_sem_preco_fresco():
    s = _sinal(id="s3", status="aguardando_crivo")
    banco = BancoFake(aguardando=[s], snaps={})    # sem snapshot → não inventa movimento
    assert expirar_pendentes(banco) == 0 and banco.transicoes == []


# ---------------- alertas ----------------

def test_alerta_drawdown_dispara_uma_vez():
    banco = BancoFake(banca={"kill_switch": True, "drawdown_pct": 21.0})
    assert alerta_drawdown(banco) is True
    assert len(banco.notificacoes(tipo="alerta_drawdown")) == 1
    # segunda vez: já há pendente → não duplica
    assert alerta_drawdown(banco) is False


def test_alerta_drawdown_nao_dispara_sem_kill_switch():
    banco = BancoFake(banca={"kill_switch": False, "drawdown_pct": 5.0})
    assert alerta_drawdown(banco) is False


def test_entregar_pendentes_marca_entregue():
    pend = [{"id": 10, "tipo": "alerta_daemon", "conteudo": "daemon l0 mudo"}]
    banco = BancoFake(pendentes=pend)
    bot = BotFake()
    n = entregar_pendentes(banco, bot)
    assert n == 1 and banco.entregues == [10] and bot.enviados == ["daemon l0 mudo"]


def test_processar_pulsa_heartbeat_l3():
    banco = BancoFake()
    r = processar(banco, BotFake())
    assert isinstance(r, ResumoL3)
    assert banco.pulsos and banco.pulsos[-1][0] == "l3"


# ---------------- bot real (transporte fake) ----------------

def test_bot_telegram_envia_ok():
    chamadas = []

    def transporte(url, corpo):
        chamadas.append((url, corpo))
        return 200, b'{"ok": true, "result": {}}'

    bot = BotTelegram("TOKEN", "123", transporte=transporte)
    assert bot.enviar("oi") is True
    assert "/botTOKEN/sendMessage" in chamadas[0][0]
    assert b'"chat_id": "123"' in chamadas[0][1]


def test_bot_telegram_status_ruim_vira_false():
    bot = BotTelegram("T", "1", transporte=lambda u, c: (403, b'{"ok": false}'))
    assert bot.enviar("x") is False


def test_bot_telegram_erro_de_rede_vira_false_nao_propaga():
    """Erro de rede falando com o Telegram NUNCA sobe — devolve False (senão a
    camada de cima o confunde com falha do Supabase, bug real 24/07). Cobre tanto
    ConnectionError quanto um OSError cru (timeout/gaierror) do transporte."""
    def transporte_connectionerror(u, c):
        raise ConnectionError("getaddrinfo failed")

    def transporte_oserror(u, c):
        raise TimeoutError("timed out")  # OSError cru, não embrulhado

    assert BotTelegram("T", "1", transporte=transporte_connectionerror).enviar("x") is False
    assert BotTelegram("T", "1", transporte=transporte_oserror).enviar("x") is False
