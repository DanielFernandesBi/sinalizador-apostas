"""Vigia de heartbeats (E1.5 + auditoria P1 item P1.5).

O que se trava aqui:
  - o roster cobre o PIPELINE INTEIRO, não só os dois daemons do L0;
  - cada daemon é medido contra a PRÓPRIA cadência (limiar único era errado por
    construção: 7200 s são 240 ciclos do L2 e dois do L0);
  - o silêncio vira EPISÓDIO — um alerta na queda, um na volta, e nada nos ciclos
    do meio (a mesma cura do P1.4);
  - o alerta sobre o L3 NÃO depende do L3 para ser entregue.
"""
from sinalizador.l0_captura.vigia import (
    DAEMONS_ESPERADOS_PADRAO,
    ROSTER_PADRAO,
    DaemonEsperado,
    alertar_mudos,
    anunciar_voltas,
    avaliar_daemons,
    daemons_mudos,
    rodar_vigia,
)


class BancoFake:
    """Simula `vw_saude_daemons` + os episódios da 0019 (índice parcial por daemon)."""

    def __init__(self, saude):
        self._saude = saude
        self.inseridos = []
        self.pulsos = []
        self.episodios = {}     # daemon -> dict(id, aberto, ...)
        self._seq = 0

    def saude_daemons(self):
        return self._saude

    def inserir(self, tabela, registro):
        self.inseridos.append((tabela, registro))
        return {"id": len(self.inseridos), **registro}

    def pulsar(self, daemon, detalhe=None):
        self.pulsos.append((daemon, detalhe))

    def abrir_episodio_silencio(self, daemon, silencio_s=None, limiar_s=None):
        ep = self.episodios.get(daemon)
        if ep and ep["aberto"]:
            return {"abriu": False, "episodio_id": ep["id"], "daemon": daemon,
                    "motivo": "episodio_ja_aberto"}
        self._seq += 1
        self.episodios[daemon] = {"id": self._seq, "aberto": True,
                                  "silencio_s": silencio_s, "limiar_s": limiar_s}
        return {"abriu": True, "episodio_id": self._seq, "daemon": daemon}

    def encerrar_episodio_silencio(self, daemon):
        ep = self.episodios.get(daemon)
        if not ep or not ep["aberto"]:
            return {"encerrou": False, "episodio_id": None, "daemon": daemon}
        ep["aberto"] = False
        return {"encerrou": True, "episodio_id": ep["id"], "daemon": daemon,
                "duracao_s": 1800.0}


class BotFake:
    def __init__(self, ok=True):
        self.ok = ok
        self.enviados = []

    def enviar(self, texto):
        self.enviados.append(texto)
        return self.ok


def _vivos(*nomes, segundos=10.0):
    return [{"daemon": n, "segundos_em_silencio": segundos} for n in nomes]


# ------------------------------------------------------- roster e limiar

def test_roster_padrao_cobre_o_pipeline_inteiro():
    """O buraco do P1.5: L1, L2, L3 e L4 pulsavam e ninguém lia."""
    assert set(DAEMONS_ESPERADOS_PADRAO) == {
        "l0_referencia", "l1", "l2", "l3", "l4", "vigia"}


def test_l0_varejo_fica_fora_do_roster_enquanto_for_no_op():
    """Ele roda em degradação segura (não há região `br` na The Odds API) e o varejo
    da `eu` já vem pelo l0_referencia. Esperar pulso dele abriria um episódio de
    silêncio que nunca fecha — e alerta que nunca fecha ensina a ignorar alerta."""
    assert "l0_varejo" not in DAEMONS_ESPERADOS_PADRAO


def test_limiar_sai_da_cadencia_de_cada_daemon():
    """Um número só não serve: o mesmo silêncio é catástrofe para um e normal para
    outro. E o piso impede que um daemon rápido alerte por um soluço de rede."""
    por_nome = {d.nome: d for d in ROSTER_PADRAO}
    assert por_nome["l0_referencia"].limiar_s == 10800.0    # 3600 × 3
    assert por_nome["l4"].limiar_s == 2700.0                # 900 × 3
    assert por_nome["l2"].limiar_s == 300.0                 # 30 × 3 = 90 → piso 300


def test_l2_mudo_ha_dez_minutos_e_detectado():
    """Com o limiar antigo (7200 s) isto passaria batido por mais de duas horas —
    com os candidatos empilhados em `aguardando_crivo` e ninguém sabendo."""
    banco = BancoFake(_vivos("l2", segundos=600.0))
    mudos = daemons_mudos(banco, esperados=(DaemonEsperado("l2", 30.0),))
    assert [m["daemon"] for m in mudos] == ["l2"]
    assert mudos[0]["limiar_s"] == 300.0


def test_daemon_saudavel_nao_alerta():
    banco = BancoFake(_vivos("l0_referencia", "l0_varejo", segundos=30.0))
    mudos, vivos = avaliar_daemons(banco, esperados=("l0_referencia", "l0_varejo"))
    assert mudos == []
    assert vivos == ["l0_referencia", "l0_varejo"]


def test_daemon_que_nunca_pulsou_e_mudo_com_segundos_none():
    """`None` não pode virar 0: 'nunca pulsou' é ausência de referência, não
    silêncio zero — e o episódio grava NULL, não um número inventado."""
    banco = BancoFake(_vivos("l1"))
    mudos = daemons_mudos(banco, esperados=("l1", "l4"))
    l4 = [m for m in mudos if m["daemon"] == "l4"][0]
    assert l4["motivo"] == "nunca pulsou" and l4["segundos"] is None


def test_nome_solto_desconhecido_usa_limiar_folgado():
    """Cadência não declarada → não se inventa piso curto (viraria alerta falso)."""
    banco = BancoFake([{"daemon": "daemon_novo", "segundos_em_silencio": 4000.0}])
    assert daemons_mudos(banco, esperados=("daemon_novo",)) == []


# ------------------------------------------------------- episódio (anti-spam)

def test_alerta_uma_vez_por_queda_e_nao_por_ciclo():
    """O defeito irmão do P1.4: vigia a cada 30 min num fim de semana dava ~100
    notificações idênticas."""
    banco = BancoFake(_vivos("l2", segundos=9000.0))
    roster = (DaemonEsperado("l2", 30.0),)
    rodar_vigia(banco, esperados=roster)
    rodar_vigia(banco, esperados=roster)
    rodar_vigia(banco, esperados=roster)
    alertas = [r for _, r in banco.inseridos if r["tipo"] == "alerta_daemon"]
    assert len(alertas) == 1
    assert "l2" in alertas[0]["conteudo"]


def test_volta_encerra_episodio_e_anuncia():
    banco = BancoFake(_vivos("l2", segundos=9000.0))
    roster = (DaemonEsperado("l2", 30.0),)
    rodar_vigia(banco, esperados=roster)          # cai
    banco._saude = _vivos("l2", segundos=10.0)
    rodar_vigia(banco, esperados=roster)          # volta
    conteudos = [r["conteudo"] for _, r in banco.inseridos]
    assert len(conteudos) == 2
    assert "voltou a pulsar" in conteudos[1] and "1800s" in conteudos[1]


def test_daemon_sempre_vivo_nunca_anuncia_volta():
    """Sem `encerrou=false`, todo ciclo com o pipeline de pé viraria um 'voltou'."""
    banco = BancoFake(_vivos("l2", segundos=10.0))
    roster = (DaemonEsperado("l2", 30.0),)
    rodar_vigia(banco, esperados=roster)
    rodar_vigia(banco, esperados=roster)
    assert banco.inseridos == []


def test_nova_queda_depois_da_volta_alerta_de_novo():
    banco = BancoFake(_vivos("l2", segundos=9000.0))
    roster = (DaemonEsperado("l2", 30.0),)
    rodar_vigia(banco, esperados=roster)
    banco._saude = _vivos("l2", segundos=10.0)
    rodar_vigia(banco, esperados=roster)
    banco._saude = _vivos("l2", segundos=9000.0)
    rodar_vigia(banco, esperados=roster)
    quedas = [r for _, r in banco.inseridos if "> limiar" in r["conteudo"]]
    voltas = [r for _, r in banco.inseridos if "voltou" in r["conteudo"]]
    assert len(quedas) == 2 and len(voltas) == 1


def test_alertar_grava_notificacao_alerta_daemon():
    banco = BancoFake([])
    inseridas = alertar_mudos(banco, [{"daemon": "l0_varejo", "segundos": None,
                                       "limiar_s": 10800.0, "motivo": "nunca pulsou"}])
    assert len(inseridas) == 1
    tabela, reg = banco.inseridos[0]
    assert tabela == "notificacoes"
    # sem `status` explícito: o default do schema é 'pendente', e a outbox do L3
    # (migration 0010) é quem entrega. Cravar o estado aqui duplicaria a decisão.
    assert reg["tipo"] == "alerta_daemon" and "status" not in reg
    assert "l0_varejo" in reg["conteudo"] and reg["sinal_id"] is None


# ------------------------------------------- o alerta sobre o L3 não depende do L3

def test_l3_mudo_e_avisado_por_fora_da_outbox():
    """O acoplamento: quem esvazia a outbox é o L3. Se o mudo É o L3, o alerta sobre
    a morte dele esperaria ele ressuscitar para se anunciar."""
    banco = BancoFake(_vivos("l3", segundos=9000.0))
    bot = BotFake()
    rodar_vigia(banco, esperados=(DaemonEsperado("l3", 30.0),), bot=bot)
    assert len(bot.enviados) == 1 and "l3" in bot.enviados[0]
    _, reg = banco.inseridos[0]
    # `interno` = registro de auditoria, nunca vai ao bot (0010) — assim o L3, ao
    # voltar, não reenvia o que o vigia já entregou.
    assert reg["status"] == "interno" and reg["entregue"] is True
    assert "[entregue-fora-da-outbox]" in reg["conteudo"]


def test_l3_mudo_sem_bot_ainda_enfileira():
    """Alerta preso na fila é pior que entregue — e MUITO melhor que nenhum."""
    banco = BancoFake(_vivos("l3", segundos=9000.0))
    rodar_vigia(banco, esperados=(DaemonEsperado("l3", 30.0),), bot=None)
    _, reg = banco.inseridos[0]
    assert "status" not in reg   # nasce pendente


def test_envio_direto_que_falha_cai_na_outbox():
    banco = BancoFake(_vivos("l3", segundos=9000.0))
    bot = BotFake(ok=False)
    rodar_vigia(banco, esperados=(DaemonEsperado("l3", 30.0),), bot=bot)
    assert len(banco.inseridos) == 1
    _, reg = banco.inseridos[0]
    assert "status" not in reg and "[entregue-fora-da-outbox]" not in reg["conteudo"]


def test_l0_mudo_nao_usa_a_saida_de_emergencia():
    """A saída direta existe para UM caso. Usá-la sempre duplicaria a entrega."""
    banco = BancoFake(_vivos("l0_varejo", segundos=99000.0))
    bot = BotFake()
    rodar_vigia(banco, esperados=(DaemonEsperado("l0_varejo", 3600.0),), bot=bot)
    assert bot.enviados == []
    _, reg = banco.inseridos[0]
    assert "status" not in reg


# ------------------------------------------------------- o vigia se declara

def test_vigia_pulsa_para_que_a_propria_lacuna_apareca():
    banco = BancoFake(_vivos("l1", segundos=10.0))
    rodar_vigia(banco, esperados=(DaemonEsperado("l1", 60.0),))
    assert [d for d, _ in banco.pulsos] == ["vigia"]


def test_falha_no_pulso_nao_derruba_o_vigia():
    """Pulso é diagnóstico: quebrar nele apagaria a vigilância inteira."""
    banco = BancoFake(_vivos("l2", segundos=9000.0))

    def explode(daemon, detalhe=None):
        raise RuntimeError("banco fora do ar")

    banco.pulsar = explode
    mudos = rodar_vigia(banco, esperados=(DaemonEsperado("l2", 30.0),))
    assert [m["daemon"] for m in mudos] == ["l2"]
    assert len(banco.inseridos) == 1


def test_anunciar_voltas_isolado_nao_alerta_sem_queda():
    banco = BancoFake([])
    assert anunciar_voltas(banco, ["l1", "l2"]) == []
    assert banco.inseridos == []
