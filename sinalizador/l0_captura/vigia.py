"""E1.5 — Vigia de heartbeats: silêncio de daemon → alerta.

Cada daemon pulsa a cada ciclo (`banco.pulsar`); o vigia lê `vw_saude_daemons` e,
para todo daemon do ROSTER em silêncio além do SEU limiar (ou que nunca pulsou),
abre um EPISÓDIO e registra uma notificação. Quando o daemon volta, o episódio
fecha e a volta é anunciada.

Três coisas mudaram na auditoria P1 (item P1.5) — e as três são a mesma pergunta:
*o pipeline inteiro está vivo?*

1. **O roster.** Antes eram dois daemons (`l0_referencia`, `l0_varejo`). L1, L2, L3
   e L4 pulsam desde sempre e ninguém lia. O L2 podia estar morto há um dia, com os
   candidatos empilhados em `aguardando_crivo`, e nada dizia nada — mudez perfeita,
   indistinguível de "não houve oportunidade". Este sistema tem exatamente um
   produto (avisar) e um jeito de falhar em silêncio (não avisar).

2. **O limiar por daemon.** Um número só era errado por construção: 7200 s são 240
   ciclos perdidos do L2 (cadência 30 s) e dois ciclos do L0 (3600 s). O limiar sai
   da cadência: `max(piso_s, cadencia_s × tolerancia_ciclos)`.

3. **O episódio.** Antes era uma notificação por ciclo do vigia; agora é uma na
   queda e uma na volta (migration 0019, mesmo desenho do P1.4).

QUEM VIGIA O VIGIA: em banda, ninguém — um processo morto não se denuncia. O que dá
para fazer é ele PULSAR e entrar no próprio roster, para que a lacuna fique
registrada e visível assim que voltar. A garantia externa é o systemd (E0.5).

O ROSTER É PARÂMETRO OPERACIONAL, não gate da Doutrina: depende da cadência com que
cada daemon é de fato subido. Por isso vive aqui e é sobreponível pela CLI — mas
PRECISA acompanhar as units do systemd. Roster que mente sobre a cadência produz
alerta falso (limiar curto demais) ou mudez (longo demais). Ver PC-ROSTER-VIGIA.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional, Protocol

_log = logging.getLogger(__name__)

DAEMON = "vigia"

# Piso de limiar: abaixo disto não se alerta, por mais rápido que seja o daemon. Um
# L2 de cadência 30 s com tolerância 3 daria 90 s — perto demais de um soluço de rede
# ou de um ciclo que demorou a mais. O piso troca sensibilidade por silêncio.
PISO_LIMIAR_S_PADRAO = 300.0

# Três ciclos perdidos. Um pode ser transiente (rede, API lenta); três seguidos são
# padrão, não azar.
TOLERANCIA_CICLOS_PADRAO = 3.0


@dataclass(frozen=True)
class DaemonEsperado:
    """Contrato operacional de um daemon: de quanto em quanto tempo ele deve pulsar.

    `cadencia_s` tem que bater com o `--intervalo-s` da unit correspondente. Os
    valores do roster são os DEFAULTS das CLIs; subindo um daemon com outro
    intervalo, o roster precisa acompanhar (`--daemon nome:cadencia[:tolerancia]`).
    """
    nome: str
    cadencia_s: float
    tolerancia_ciclos: float = TOLERANCIA_CICLOS_PADRAO
    piso_s: float = PISO_LIMIAR_S_PADRAO

    @property
    def limiar_s(self) -> float:
        return max(self.piso_s, self.cadencia_s * self.tolerancia_ciclos)


# Cadências = defaults das CLIs de cada camada em 26/07/2026:
#   l0_* --intervalo-s 3600 (o adaptativo desce a 300, o que só ENCURTA o silêncio
#   real e portanto nunca gera alerta falso) · l1 60 · l2 30 · l3 30 · l4 900 ·
#   vigia 1800.
ROSTER_PADRAO: tuple[DaemonEsperado, ...] = (
    DaemonEsperado("l0_referencia", 3600.0),
    DaemonEsperado("l0_varejo", 3600.0),
    DaemonEsperado("l1", 60.0),
    DaemonEsperado("l2", 30.0),
    DaemonEsperado("l3", 30.0),
    DaemonEsperado("l4", 900.0),
    DaemonEsperado(DAEMON, 1800.0),
)

# Compatibilidade com quem só quer os nomes (CLI `--esperados`).
DAEMONS_ESPERADOS_PADRAO = tuple(d.nome for d in ROSTER_PADRAO)

# Limiar folgado histórico: hoje só se aplica a nome solto que o roster não conhece.
LIMIAR_SILENCIO_S_PADRAO = 7200.0


class BancoVigia(Protocol):
    def saude_daemons(self) -> list[dict[str, Any]]: ...
    def inserir(self, tabela: str, registro: dict[str, Any]) -> dict[str, Any]: ...
    def pulsar(self, daemon: str, detalhe: Optional[dict[str, Any]] = None) -> None: ...
    def abrir_episodio_silencio(self, daemon: str, silencio_s, limiar_s) -> dict[str, Any]: ...
    def encerrar_episodio_silencio(self, daemon: str) -> dict[str, Any]: ...


class Bot(Protocol):
    def enviar(self, texto: str) -> bool: ...


def _roster(esperados) -> tuple[DaemonEsperado, ...]:
    """Aceita roster tipado OU lista de nomes (compatibilidade da CLI antiga)."""
    padrao = {d.nome: d for d in ROSTER_PADRAO}
    saida: list[DaemonEsperado] = []
    for item in esperados:
        if isinstance(item, DaemonEsperado):
            saida.append(item)
        else:
            # Nome solto: usa a cadência conhecida se houver; senão, o limiar folgado
            # antigo — nunca um piso curto, que viraria alerta falso num daemon cuja
            # cadência real ninguém declarou.
            saida.append(padrao.get(item) or DaemonEsperado(
                str(item), LIMIAR_SILENCIO_S_PADRAO, tolerancia_ciclos=1.0,
                piso_s=LIMIAR_SILENCIO_S_PADRAO))
    return tuple(saida)


def avaliar_daemons(
    banco: BancoVigia, *, esperados=ROSTER_PADRAO, limiar_s: Optional[float] = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Separa o roster em (mudos, vivos). Cada mudo carrega o SEU limiar.

    `limiar_s` sobrepõe o limiar de TODOS (escotilha de operação/teste). Fora disso,
    cada daemon é medido contra a própria cadência.
    """
    saude = {r["daemon"]: r for r in banco.saude_daemons()}
    mudos: list[dict[str, Any]] = []
    vivos: list[str] = []
    for d in _roster(esperados):
        limiar = float(limiar_s) if limiar_s is not None else d.limiar_s
        linha = saude.get(d.nome)
        if linha is None:
            # NUNCA PULSOU não é "silêncio zero" — é ausência de referência. Segue
            # como mudo com `segundos=None`, e o episódio grava NULL, não 0.
            mudos.append({"daemon": d.nome, "segundos": None, "limiar_s": limiar,
                          "motivo": "nunca pulsou"})
            continue
        segundos = linha.get("segundos_em_silencio")
        if segundos is not None and float(segundos) > limiar:
            mudos.append({"daemon": d.nome, "segundos": float(segundos), "limiar_s": limiar,
                          "motivo": f"silêncio {float(segundos):.0f}s > limiar {limiar:.0f}s "
                                    f"(cadência {d.cadencia_s:.0f}s)"})
        else:
            vivos.append(d.nome)
    return mudos, vivos


def daemons_mudos(
    banco: BancoVigia, *, limiar_s: Optional[float] = None, esperados=ROSTER_PADRAO,
) -> list[dict[str, Any]]:
    """Só os mudos (assinatura histórica, agora com limiar por daemon)."""
    return avaliar_daemons(banco, esperados=esperados, limiar_s=limiar_s)[0]


def _conteudo_queda(m: dict[str, Any]) -> str:
    return f"[alerta_daemon] {m['daemon']}: {m['motivo']}"


def _conteudo_volta(daemon: str, duracao_s) -> str:
    if duracao_s is None:
        return f"[alerta_daemon] {daemon}: voltou a pulsar"
    return (f"[alerta_daemon] {daemon}: voltou a pulsar após "
            f"{float(duracao_s):.0f}s de silêncio")


def _registrar(banco: BancoVigia, conteudo: str, *, bot: Optional[Bot],
               fora_da_outbox: bool) -> dict[str, Any]:
    """Grava a notificação — e, quando a outbox está morta, entrega por fora.

    O ACOPLAMENTO que isto resolve: a notificação nasce `pendente` e quem esvazia a
    outbox é o L3. Se o daemon mudo É o L3, o alerta sobre a morte do L3 fica na fila
    esperando o L3 ressuscitar para se anunciar — o único alerta que nunca chega é o
    do componente cuja morte cala o sistema inteiro. Detectado o L3 mudo e havendo
    bot, o vigia envia DIRETO e grava a linha como `interno` (status que a 0010
    define como "registro de auditoria: nunca vai ao bot"), para que o L3, ao voltar,
    não reenvie o que já foi entregue.

    Se o envio direto falha — ou não há bot —, a linha nasce `pendente` mesmo assim:
    alerta preso na fila é pior que alerta entregue, e muito melhor que nenhum.
    """
    if fora_da_outbox and bot is not None:
        try:
            if bot.enviar(conteudo):
                return banco.inserir("notificacoes", {
                    "sinal_id": None, "tipo": "alerta_daemon", "canal": "telegram",
                    "conteudo": f"{conteudo} [entregue-fora-da-outbox]",
                    "status": "interno", "entregue": True})
        except Exception:
            _log.exception("envio direto do vigia falhou — cai na outbox")
    if fora_da_outbox:
        _log.error("alerta sobre o L3 vai para a outbox que o próprio L3 esvazia — "
                   "pode não ser entregue até o L3 voltar", extra={"conteudo": conteudo})
    return banco.inserir("notificacoes", {
        "sinal_id": None, "tipo": "alerta_daemon", "canal": "telegram",
        "conteudo": conteudo})   # status default 'pendente' (outbox do L3)


def alertar_mudos(banco: BancoVigia, mudos: list[dict[str, Any]], *,
                  bot: Optional[Bot] = None) -> list[dict[str, Any]]:
    """Abre episódio por daemon mudo e alerta UMA vez por queda.

    A notificação só nasce quando o episódio ABRE. Episódio já aberto = mesma queda:
    o vigia continua rodando e continua vendo o daemon mudo, mas não repete o aviso.
    """
    inseridas: list[dict[str, Any]] = []
    l3_mudo = any(m["daemon"] == "l3" for m in mudos)
    for m in mudos:
        ep = banco.abrir_episodio_silencio(m["daemon"], m.get("segundos"), m.get("limiar_s"))
        if not ep.get("abriu"):
            _log.info("daemon segue mudo — episódio já aberto, sem novo alerta", extra=m)
            continue
        inseridas.append(_registrar(banco, _conteudo_queda(m), bot=bot,
                                    fora_da_outbox=l3_mudo))
        _log.warning("daemon mudo — episódio aberto e alerta registrado", extra=m)
    return inseridas


def anunciar_voltas(banco: BancoVigia, vivos: list[str], *,
                    bot: Optional[Bot] = None) -> list[dict[str, Any]]:
    """Fecha o episódio de quem voltou e anuncia a volta.

    `encerrou=false` é o caso normal (o daemon nunca caiu) e não gera nada — sem
    isso, todo ciclo com o pipeline inteiro de pé viraria um alerta de "voltou".
    """
    inseridas: list[dict[str, Any]] = []
    for nome in vivos:
        ep = banco.encerrar_episodio_silencio(nome)
        if not ep.get("encerrou"):
            continue
        inseridas.append(_registrar(banco, _conteudo_volta(nome, ep.get("duracao_s")),
                                    bot=bot, fora_da_outbox=False))
        _log.warning("daemon voltou — episódio encerrado",
                     extra={"daemon": nome, "duracao_s": ep.get("duracao_s")})
    return inseridas


def rodar_vigia(
    banco: BancoVigia, *, limiar_s: Optional[float] = None, esperados=ROSTER_PADRAO,
    bot: Optional[Bot] = None,
) -> list[dict[str, Any]]:
    """Uma passada: detecta, alerta as quedas, anuncia as voltas e PULSA.

    O pulso vem por último e sempre: é o que permite ao próprio vigia aparecer em
    `vw_saude_daemons` e ter a própria lacuna registrada. Devolve os mudos.
    """
    mudos, vivos = avaliar_daemons(banco, esperados=esperados, limiar_s=limiar_s)
    if mudos:
        alertar_mudos(banco, mudos, bot=bot)
    anunciar_voltas(banco, vivos, bot=bot)
    try:
        banco.pulsar(DAEMON, {"mudos": [m["daemon"] for m in mudos], "vivos": len(vivos)})
    except Exception:  # pulso é diagnóstico: falhar nele não pode derrubar o vigia
        _log.exception("falha ao pulsar o vigia")
    return mudos
