"""Testes do L3 (notificação): cartão, frescor de preço, expiração, outbox, alertas.

Tudo com fakes: nem rede, nem token, nem Supabase. O núcleo depende só do Protocol
`Bot` e da fachada do `Banco`. O `BancoFake` simula a semântica da outbox da
migration 0010 (a fidelidade das funções SQL foi verificada contra o banco real).
"""
import pytest

from sinalizador.comum.tempo import para_datetime
from sinalizador.l3_notifica.bot import BotTelegram
from sinalizador.l3_notifica.cartao import formatar_cartao, janela_fechou, odd_atual, preco_caiu
from sinalizador.l3_notifica.notifica import (
    ResumoL3,
    alerta_drawdown,
    enfileirar_cartoes,
    entregar_pendentes,
    expirar_pendentes,
    processar,
)

ODD_MIN = 1.90
AGORA_ISO = "2026-07-21T12:05:00Z"
AGORA = para_datetime(AGORA_ISO)
TS_FRESCO = "2026-07-21T12:00:00Z"     # 300 s — dentro de snapshot_idade_max_s (600)
TS_VELHO = "2026-07-20T12:00:00Z"      # ~1 dia — fora
IDADE_MAX = 600.0


class GatesFake:
    def __init__(self, snapshot_idade_max_s=IDADE_MAX):
        self._v = {"snapshot_idade_max_s": snapshot_idade_max_s}

    def get(self, nome):
        return self._v[nome]


def _sinal(id="s1", *, status="confirmado", odd_venue=2.05, odd_min=ODD_MIN, linha=None):
    return {
        "id": id, "evento_id": "ev1", "casa_venue_id": "c-b365", "status": status,
        "mercado": "1x2", "selecao": "1", "linha": linha, "odd_venue": odd_venue,
        "edge_liquido_pct": 3.1, "stake_pct": 1.2, "odd_minima_aceitavel": odd_min,
        "gatilho": "value_bet", "gatilho_anomalo": False,
        "dossie": {"evento": {"liga": "Premier League", "partida": "A x B", "casa_venue": "bet365_br"},
                   "liquidez": {"sombra_varejo": True}, "banca_origem": "papel"},
    }


def _snap(odd, ts=TS_FRESCO):
    return {"odd": odd, "ts_fonte": ts, "ts_captura": ts}


class BotFake:
    def __init__(self, ok=True):
        self.ok = ok
        self.enviados = []

    def enviar(self, texto):
        self.enviados.append(texto)
        return self.ok


class ErroUnicidade(RuntimeError):
    """Equivale ao 23505 de ux_notificacao_cartao."""


class BancoFake:
    def __init__(self, *, confirmados=None, aguardando=None, snaps=None, crivos=None,
                 eventos=None, notif_por_sinal=None, pendentes=None, banca=None,
                 reserva=None, reserva_levanta=False, max_tentativas=6):
        self._confirmados = confirmados or []
        self._aguardando = aguardando or []
        self._snaps = snaps or {}
        self._crivos = crivos or {}
        self._eventos = eventos or {}
        self._notif_por_sinal = notif_por_sinal or {}
        self._banca = banca
        self.inseridos = []
        self.transicoes = []
        self.entregues = []
        self.devolvidas = []
        self.pulsos = []
        self.ordem = []                     # ordem real das fases (achado da ordem)
        self._fila = list(pendentes or [])  # linhas em pendente/enviando
        self._prox_id = 100
        # P0.8 — posição de papel (fn_reservar_exposicao_papel, verificada no banco real)
        self.reservas = []
        self._reserva = reserva if reserva is not None else {"reservado": True}
        self._reserva_levanta = reserva_levanta
        # P1.3/P1.4/P1.6 (migration 0018)
        self.entregas = {}
        self.episodios = []
        self.episodio_aberto = False
        self._max_tentativas = max_tentativas

    # -- leituras --
    def sinais_por_status(self, status, limite=200):
        self.ordem.append("enfileirar")
        return self._confirmados if status == "confirmado" else []

    def sinais_aguardando_crivo(self, limite=200, agora_iso=None):
        self.ordem.append("expirar")
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
        return [n for n in self._fila if n.get("status") in ("pendente", "enviando")]

    def banca_atual(self):
        return self._banca

    # -- escritas --
    def inserir(self, tabela, registro):
        if tabela == "notificacoes" and registro.get("tipo") == "sinal":
            # ux_notificacao_cartao: um cartão por sinal
            if any(n.get("tipo") == "sinal" and n.get("sinal_id") == registro.get("sinal_id")
                   for (_t, n) in self.inseridos):
                raise ErroUnicidade(
                    'duplicate key value violates unique constraint "ux_notificacao_cartao"')
        self._prox_id += 1
        row = {"id": self._prox_id, "status": "pendente", **registro}
        self.inseridos.append((tabela, row))
        if tabela == "notificacoes" and row["status"] == "pendente":
            self._fila.append(row)
        return row

    def transicionar_status_sinal(self, sinal_id, novo_status):
        self.transicoes.append((sinal_id, novo_status))
        return {"id": sinal_id, "status": novo_status}

    def reivindicar_notificacoes(self, agora_iso, limite=200, reclaim_s=300.0):
        self.ordem.append("enviar")
        reivindicadas = [n for n in self._fila if n.get("status") == "pendente"][:limite]
        for n in reivindicadas:
            n["status"] = "enviando"
            n["tentativas"] = n.get("tentativas", 0) + 1
        return reivindicadas

    def marcar_notificacao_entregue(self, notif_id, agora_iso=None):
        self.entregues.append(notif_id)
        for n in self._fila:
            if n["id"] == notif_id:
                n["status"] = "entregue"
        return True

    def devolver_notificacao(self, notif_id, erro=None, agora_iso=None):
        """Espelha `fn_devolver_notificacao` (0018): backoff e desistência."""
        self.devolvidas.append(notif_id)
        for n in self._fila:
            if n["id"] == notif_id and n["status"] == "enviando":
                tent = n.get("tentativas", 1)
                if tent >= self._max_tentativas:
                    n["status"] = "morta"
                    if n.get("tipo") == "sinal" and n.get("sinal_id"):
                        self.entregas[n["sinal_id"]] = {"desfecho": "nao_entregue_falha"}
                    return {"devolvido": False, "morta": True, "tentativas": tent}
                n["status"] = "pendente"
                return {"devolvido": True, "espera_s": 30 * 2 ** (tent - 1)}
        return {"devolvido": False, "motivo": "nao_estava_enviando"}

    # -- P1.3 / P1.4 (espelham as RPCs da 0018) --
    def registrar_tentativa_cartao(self, sinal_id, motivo, agora_iso=None):
        e = self.entregas.setdefault(sinal_id, {"tentativas": 0, "desfecho": None})
        if e.get("desfecho"):
            return {"registrado": False, "motivo": "desfecho_ja_selado"}
        e["tentativas"] += 1
        e["ultimo_motivo"] = motivo
        return {"registrado": True, "tentativas": e["tentativas"]}

    def registrar_entrega_cartao(self, sinal_id, agora_iso=None):
        e = self.entregas.setdefault(sinal_id, {"tentativas": 0, "desfecho": None})
        if e.get("desfecho"):
            return {"registrado": False, "motivo": "desfecho_ja_selado"}
        e["desfecho"] = "entregue"
        e["entregue_em"] = agora_iso
        return {"registrado": True}

    def abrir_episodio_kill_switch(self, pico=None, drawdown_pct=None, agora_iso=None):
        if self.episodio_aberto:
            return {"abriu": False, "motivo": "episodio_ja_aberto"}
        self.episodio_aberto = True
        self.episodios.append({"pico": pico, "drawdown_pct": drawdown_pct})
        return {"abriu": True, "episodio_id": len(self.episodios)}

    def encerrar_episodio_kill_switch(self, motivo=None, agora_iso=None):
        encerrou = self.episodio_aberto
        self.episodio_aberto = False
        return {"encerrou": encerrou}

    def reservar_exposicao_papel(self, sinal_id, agora_iso=None):
        self.reservas.append(sinal_id)
        if self._reserva_levanta:
            raise RuntimeError("banco fora do ar")
        return self._reserva

    def pulsar(self, daemon, detalhe=None):
        self.pulsos.append((daemon, detalhe))

    def notificacoes(self, tipo=None):
        return [r for (t, r) in self.inseridos
                if t == "notificacoes" and (tipo is None or r["tipo"] == tipo)]


def _snaps_ok(odd=2.05, ts=TS_FRESCO):
    return {("ev1", "c-b365", "1x2", "1", None): _snap(odd, ts)}


# ---------------- cartão + frescor de preço ----------------

def test_odd_atual_e_janela():
    assert odd_atual(_snap(2.0)) == 2.0
    assert odd_atual(None) is None
    assert odd_atual({"odd": None}) is None
    assert janela_fechou(1.85, 1.90) is True       # abaixo da mínima
    assert janela_fechou(None, 1.90) is True        # sem preço → fail-safe (não envia)
    assert janela_fechou(1.95, 1.90) is False
    assert preco_caiu(1.85, 1.90) is True
    assert preco_caiu(None, 1.90) is False          # ausência não expira


def test_snapshot_velho_nao_conta_como_atual():
    # O último snapshot não é necessariamente ATUAL: acima do gate, é como se não
    # houvesse preço (mesmo gate do L1 — Doutrina §3, janela de validade do dado).
    assert odd_atual(_snap(2.05, TS_FRESCO), agora=AGORA, idade_max_s=IDADE_MAX) == 2.05
    assert odd_atual(_snap(2.05, TS_VELHO), agora=AGORA, idade_max_s=IDADE_MAX) is None
    # fronteira: exatamente no limite ainda vale
    limite = "2026-07-21T11:55:00Z"                 # 600 s
    assert odd_atual(_snap(2.05, limite), agora=AGORA, idade_max_s=IDADE_MAX) == 2.05
    # sem carimbo não se afirma frescor
    assert odd_atual({"odd": 2.05}, agora=AGORA, idade_max_s=IDADE_MAX) is None


def test_formatar_cartao_tem_o_essencial():
    txt = formatar_cartao(_sinal(), {"verdict": "CONFIRMA", "observacao": "linha estável"},
                          {"liga": "Premier League", "mandante": "A", "visitante": "B"},
                          odd_atual_venue=2.05)
    assert "SINAL CONFIRMADO" in txt and "SOMBRA" in txt and "banca de papel" in txt
    assert "MÍNIMA aceitável: 1.900" in txt
    assert "Edge líquido: 3.10%" in txt and "Stake: 1.20%" in txt
    assert "linha estável" in txt


# ---------------- enfileiramento (grava, NÃO envia) ----------------

def test_cartao_e_gravado_pendente_antes_de_qualquer_envio():
    # O coração da outbox: enfileirar não toca no bot.
    banco = BancoFake(confirmados=[_sinal()], snaps=_snaps_ok(),
                      crivos={"s1": {"verdict": "CONFIRMA", "observacao": None}})
    r = enfileirar_cartoes(banco, agora=AGORA, idade_max_s=IDADE_MAX)
    assert r.enfileirados == 1 and r.suprimidos == 0
    nota = banco.notificacoes(tipo="sinal")[0]
    assert nota["status"] == "pendente"


def test_janela_fechada_conta_tentativa_sem_selar_desfecho():
    """P1.3: antes, cada ciclo com janela fechada inseria uma notificação
    administrativa nova — centenas por sinal. Agora é UMA linha com contador. E não
    sela: o preço pode voltar acima da mínima antes do apito."""
    banco = BancoFake(confirmados=[_sinal()], snaps=_snaps_ok(odd=1.80))
    r = enfileirar_cartoes(banco, agora=AGORA, idade_max_s=IDADE_MAX, agora_iso=AGORA_ISO)
    assert r.enfileirados == 0 and r.suprimidos == 1
    assert banco.notificacoes(tipo="sinal") == []
    assert banco.notificacoes(tipo="administrativo") == []   # nada de linha por ciclo
    assert banco.entregas["s1"]["tentativas"] == 1
    assert banco.entregas["s1"]["ultimo_motivo"] == "preco_abaixo_da_minima"
    assert banco.entregas["s1"]["desfecho"] is None          # ainda pode virar cartão


def test_ciclos_repetidos_de_supressao_nao_multiplicam_linhas():
    """O achado do P1.3 em uma linha: com o L3 a cada 30 s e um sinal confirmado
    horas antes do apito, o modelo antigo escrevia centenas de registros."""
    banco = BancoFake(confirmados=[_sinal()], snaps=_snaps_ok(odd=1.80))
    for _ in range(5):
        enfileirar_cartoes(banco, agora=AGORA, idade_max_s=IDADE_MAX, agora_iso=AGORA_ISO)
    assert len(banco.entregas) == 1
    assert banco.entregas["s1"]["tentativas"] == 5
    assert banco.notificacoes(tipo="administrativo") == []


def test_frescor_e_preco_sao_motivos_distintos():
    """A distinção alimenta o P0.7: preço que caiu é perda de MERCADO; ausência de
    preço atual é falha da CAPTURA."""
    banco = BancoFake(confirmados=[_sinal()], snaps={})           # sem snapshot algum
    enfileirar_cartoes(banco, agora=AGORA, idade_max_s=IDADE_MAX, agora_iso=AGORA_ISO)
    assert banco.entregas["s1"]["ultimo_motivo"] == "frescor_sem_preco_atual"


def test_snapshot_velho_suprime_o_cartao():
    # Antes: preço de um dia atrás valia como atual e o cartão saía.
    banco = BancoFake(confirmados=[_sinal()], snaps=_snaps_ok(odd=2.05, ts=TS_VELHO))
    r = enfileirar_cartoes(banco, agora=AGORA, idade_max_s=IDADE_MAX)
    assert r.enfileirados == 0 and r.suprimidos == 1


def test_sem_preco_nao_enfileira():
    banco = BancoFake(confirmados=[_sinal()], snaps={})
    r = enfileirar_cartoes(banco, agora=AGORA, idade_max_s=IDADE_MAX)
    assert r.enfileirados == 0 and r.suprimidos == 1


def test_sinal_ja_com_cartao_nao_reenfileira():
    banco = BancoFake(confirmados=[_sinal()], snaps=_snaps_ok(),
                      notif_por_sinal={"s1": ["sinal"]})
    assert enfileirar_cartoes(banco, agora=AGORA, idade_max_s=IDADE_MAX).enfileirados == 0


def test_corrida_no_enfileiramento_nao_duplica_cartao():
    # Dois processos enfileirando o mesmo cartão: o índice único recusa o segundo, e
    # isso NÃO é erro — é o resultado desejado (existe um cartão).
    banco = BancoFake(confirmados=[_sinal()], snaps=_snaps_ok())
    enfileirar_cartoes(banco, agora=AGORA, idade_max_s=IDADE_MAX)
    banco._notif_por_sinal = {}                # o caminho rápido não enxerga
    r = enfileirar_cartoes(banco, agora=AGORA, idade_max_s=IDADE_MAX)
    assert r.enfileirados == 0
    assert len(banco.notificacoes(tipo="sinal")) == 1


# ---------------- envio (outbox) ----------------

def test_envio_confirmado_marca_entregue():
    banco = BancoFake(pendentes=[{"id": 10, "tipo": "alerta_daemon",
                                  "conteudo": "daemon l0 mudo", "status": "pendente"}])
    bot = BotFake()
    entregues, falhas, cartoes, _, _ = entregar_pendentes(banco, bot, agora_iso=AGORA_ISO)
    assert (entregues, falhas, cartoes) == (1, 0, 0)
    assert banco.entregues == [10] and bot.enviados == ["daemon l0 mudo"]


def test_falha_de_envio_nao_conta_como_enviado_e_devolve_a_fila():
    # O contador mentia: somava mesmo com bot.enviar() == False.
    banco = BancoFake(pendentes=[{"id": 11, "tipo": "sinal", "conteudo": "cartão",
                                  "status": "pendente"}])
    bot = BotFake(ok=False)
    entregues, falhas, cartoes, _, _ = entregar_pendentes(banco, bot, agora_iso=AGORA_ISO)
    assert (entregues, falhas, cartoes) == (0, 1, 0)
    assert banco.entregues == [] and banco.devolvidas == [11]
    assert banco._fila[0]["status"] == "pendente"   # volta para o próximo ciclo


def test_reivindicacao_reserva_e_nao_reentrega():
    # Uma linha reivindicada sai da fila de pendentes: outro remetente não a pega.
    banco = BancoFake(pendentes=[{"id": 12, "tipo": "sinal", "conteudo": "c",
                                  "status": "pendente"}])
    entregar_pendentes(banco, BotFake(), agora_iso=AGORA_ISO)
    entregues, _, _, _, _ = entregar_pendentes(banco, BotFake(), agora_iso=AGORA_ISO)
    assert entregues == 0                            # já entregue, não reivindica de novo


# ---------------- expiração (frescor) ----------------

def test_expira_aguardando_crivo_com_preco_caido():
    banco = BancoFake(aguardando=[_sinal(id="s2", status="aguardando_crivo")],
                      snaps=_snaps_ok(odd=1.80))
    n = expirar_pendentes(banco, agora=AGORA, idade_max_s=IDADE_MAX)
    assert n == 1 and banco.transicoes == [("s2", "expirado")]


def test_nao_expira_sem_preco_fresco():
    banco = BancoFake(aguardando=[_sinal(id="s3", status="aguardando_crivo")], snaps={})
    assert expirar_pendentes(banco, agora=AGORA, idade_max_s=IDADE_MAX) == 0
    assert banco.transicoes == []


def test_preco_velho_nao_expira():
    # Preço velho não prova queda: expirar por ele seria inventar movimento.
    banco = BancoFake(aguardando=[_sinal(id="s4", status="aguardando_crivo")],
                      snaps=_snaps_ok(odd=1.80, ts=TS_VELHO))
    assert expirar_pendentes(banco, agora=AGORA, idade_max_s=IDADE_MAX) == 0


# ---------------- alertas ----------------

def test_alerta_drawdown_dispara_uma_vez():
    banco = BancoFake(banca={"kill_switch": True, "drawdown_pct": 21.0})
    assert alerta_drawdown(banco) is True
    assert len(banco.notificacoes(tipo="alerta_drawdown")) == 1
    assert alerta_drawdown(banco) is False          # já há pendente → não duplica


def test_alerta_drawdown_nao_dispara_sem_kill_switch():
    banco = BancoFake(banca={"kill_switch": False, "drawdown_pct": 5.0})
    assert alerta_drawdown(banco) is False


# ---------------- ciclo ----------------

def test_processar_expira_antes_de_enfileirar_e_envia_por_ultimo():
    # A ordem que a documentação sempre descreveu — e que o código não seguia.
    banco = BancoFake(confirmados=[_sinal()],
                      aguardando=[_sinal(id="s9", status="aguardando_crivo")],
                      snaps=_snaps_ok())
    processar(banco, BotFake(), GatesFake(), agora_iso=AGORA_ISO)
    assert banco.ordem[0] == "expirar"
    assert banco.ordem.index("enfileirar") < banco.ordem.index("enviar")


def test_processar_conta_so_o_que_saiu_e_pulsa():
    banco = BancoFake(confirmados=[_sinal()], snaps=_snaps_ok(),
                      crivos={"s1": {"verdict": "CONFIRMA", "observacao": None}})
    r = processar(banco, BotFake(), GatesFake(), agora_iso=AGORA_ISO)
    assert isinstance(r, ResumoL3)
    assert r.enfileirados == 1 and r.enviados == 1 and r.falhas_envio == 0
    assert banco.pulsos[-1][0] == "l3"
    assert banco.pulsos[-1][1]["enviados"] == 1


def test_processar_com_telegram_fora_nao_conta_enviado():
    banco = BancoFake(confirmados=[_sinal()], snaps=_snaps_ok(),
                      crivos={"s1": {"verdict": "CONFIRMA", "observacao": None}})
    r = processar(banco, BotFake(ok=False), GatesFake(), agora_iso=AGORA_ISO)
    assert r.enfileirados == 1                       # gravado
    assert r.enviados == 0 and r.falhas_envio == 1   # mas NÃO entregue
    assert banco.pulsos[-1][1]["enviados"] == 0


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


# ---------------- posição de papel: a entrega abre a exposição (P0.8) ----------------

def _pendente(id, tipo="sinal", sinal_id="s1"):
    return {"id": id, "tipo": tipo, "sinal_id": sinal_id, "conteudo": "cartão",
            "status": "pendente"}


def test_entrega_confirmada_abre_posicao_de_papel():
    """O stake nocional só passa a consumir teto DEPOIS da entrega — é o análogo do
    momento em que o dinheiro real sairia da banca. Antes do P0.8 nada reservava, e
    `vw_exposicao_aberta` ficava zerada para sempre no modo sombra."""
    banco = BancoFake(pendentes=[_pendente(21)])
    entregues, falhas, cartoes, reservas, _ = entregar_pendentes(
        banco, BotFake(), agora_iso=AGORA_ISO)
    assert (entregues, falhas, cartoes, reservas) == (1, 0, 1, 1)
    assert banco.reservas == ["s1"]


def test_envio_que_falhou_nao_reserva_exposicao():
    """Cartão que o Telegram recusou não é oportunidade entregue: reservar aqui
    inventaria uma posição que ninguém recebeu."""
    banco = BancoFake(pendentes=[_pendente(22)])
    _, falhas, _, reservas, _ = entregar_pendentes(banco, BotFake(ok=False), agora_iso=AGORA_ISO)
    assert (falhas, reservas) == (1, 0)
    assert banco.reservas == []


def test_alerta_entregue_nao_abre_posicao():
    """Só o cartão de SINAL vira posição — alerta de drawdown não é aposta."""
    banco = BancoFake(pendentes=[_pendente(23, tipo="alerta_drawdown", sinal_id=None)])
    entregues, _, cartoes, reservas, _ = entregar_pendentes(banco, BotFake(), agora_iso=AGORA_ISO)
    assert (entregues, cartoes, reservas) == (1, 0, 0)
    assert banco.reservas == []


def test_reenvio_do_mesmo_cartao_nao_duplica_posicao():
    """A recusa do banco por `ux_exposicoes_papel_sinal` não é erro: o resultado
    desejado (UMA posição) já está lá. O contador não a soma de novo."""
    banco = BancoFake(pendentes=[_pendente(24)], reserva={"reservado": False,
                                                          "motivo": "ja_reservado"})
    _, _, cartoes, reservas, _ = entregar_pendentes(banco, BotFake(), agora_iso=AGORA_ISO)
    assert (cartoes, reservas) == (1, 0)
    assert banco.reservas == ["s1"]


def test_falha_ao_reservar_nao_desfaz_entrega_confirmada():
    """A entrega já aconteceu no Telegram: derrubar o ciclo aqui devolveria o cartão
    à fila e o reenviaria. O sinal volta a contar como 'em voo' na view — que é o
    lado seguro do erro."""
    banco = BancoFake(pendentes=[_pendente(25)], reserva_levanta=True)
    entregues, falhas, cartoes, reservas, _ = entregar_pendentes(
        banco, BotFake(), agora_iso=AGORA_ISO)
    assert (entregues, falhas, cartoes, reservas) == (1, 0, 1, 0)
    assert banco.entregues == [25]


def test_resumo_do_ciclo_publica_posicoes_de_papel():
    banco = BancoFake(confirmados=[_sinal()], snaps=_snaps_ok(),
                      eventos={"ev1": {"id": "ev1", "inicio_utc": "2026-07-21T20:00:00Z"}})
    resumo = processar(banco, BotFake(), GatesFake(), agora_iso=AGORA_ISO)
    assert resumo.enviados == 1
    assert resumo.posicoes_papel == 1
    assert banco.pulsos[-1][1]["posicoes_papel"] == 1


# ---------------- P1.4: um alerta por EPISÓDIO de suspensão ----------------

BANCA_SUSPENSA = {"saldo": 780.0, "pico": 1000.0, "drawdown_pct": 22.0, "kill_switch": True}
BANCA_OK = {"saldo": 1000.0, "pico": 1000.0, "drawdown_pct": 0.0, "kill_switch": False}


def test_kill_switch_alerta_uma_vez_por_episodio():
    """Antes o anti-spam olhava só notificações PENDENTES: entregue a primeira, o
    ciclo seguinte enfileirava outra — um alerta por ciclo, indefinidamente."""
    banco = BancoFake(banca=BANCA_SUSPENSA)
    assert alerta_drawdown(banco, agora_iso=AGORA_ISO) is True
    # simula a entrega (que era o que "apagava" o anti-spam antigo)
    for n in banco._fila:
        n["status"] = "entregue"
    assert alerta_drawdown(banco, agora_iso=AGORA_ISO) is False
    assert alerta_drawdown(banco, agora_iso=AGORA_ISO) is False
    assert len(banco.notificacoes(tipo="alerta_drawdown")) == 1
    assert len(banco.episodios) == 1


def test_nova_suspensao_depois_de_recuperar_volta_a_alertar():
    """O episódio fecha quando o kill switch desarma — senão uma suspensão futura
    ficaria silenciada para sempre."""
    banco = BancoFake(banca=BANCA_SUSPENSA)
    assert alerta_drawdown(banco, agora_iso=AGORA_ISO) is True
    banco._banca = BANCA_OK
    assert alerta_drawdown(banco, agora_iso=AGORA_ISO) is False   # fecha o episódio
    assert banco.episodio_aberto is False
    banco._banca = BANCA_SUSPENSA
    assert alerta_drawdown(banco, agora_iso=AGORA_ISO) is True    # episódio novo
    assert len(banco.notificacoes(tipo="alerta_drawdown")) == 2


# ---------------- P1.6: backoff e dead-letter ----------------

def test_falha_de_envio_volta_a_fila_com_backoff():
    banco = BancoFake(pendentes=[_pendente(31)])
    _, falhas, _, _, mortas = entregar_pendentes(banco, BotFake(ok=False), agora_iso=AGORA_ISO)
    assert (falhas, mortas) == (1, 0)
    assert banco._fila[0]["status"] == "pendente"


def test_outbox_desiste_e_sela_perda_operacional():
    """Token revogado girava para sempre: `tentativas` era incrementado e nunca lido
    como limite. Agora a desistência é registrada — e vira desfecho de entrega, que é
    o que o P0.7 chama de perda operacional."""
    banco = BancoFake(pendentes=[_pendente(32)], max_tentativas=1)
    _, falhas, _, _, mortas = entregar_pendentes(banco, BotFake(ok=False), agora_iso=AGORA_ISO)
    assert (falhas, mortas) == (1, 1)
    assert banco._fila[0]["status"] == "morta"
    assert banco.entregas["s1"]["desfecho"] == "nao_entregue_falha"


def test_entrega_confirmada_sela_o_desfecho():
    banco = BancoFake(pendentes=[_pendente(33)])
    entregar_pendentes(banco, BotFake(), agora_iso=AGORA_ISO)
    assert banco.entregas["s1"]["desfecho"] == "entregue"
    assert banco.entregas["s1"]["entregue_em"] == AGORA_ISO
