"""L3 — orquestração da notificação. Sem rede/token no núcleo (Bot injetável).

Um ciclo (`processar`) faz, NESTA ordem — e o código segue a lista, não o contrário:
  1. varredura de frescor (E4.2): sinais `aguardando_crivo` cujo preço já caiu
     abaixo da mínima viram `expirado` — a ÚNICA forma de `expirado` (o trigger só
     permite transição a partir de `aguardando_crivo`) e ainda poupa o L2 de
     avaliar sinal morto. Vem PRIMEIRO de propósito: expirar antes de emitir evita
     montar cartão de um sinal que a própria varredura mataria em seguida;
  2. alerta de drawdown (E4.3): se o kill switch da banca disparou, enfileira um
     alerta (sem spam);
  3. enfileiramento dos cartões (E4.1/E4.2): para cada `confirmado` sem cartão,
     re-checa o preço — janela fechada SUPRIME (registro interno); senão a
     notificação é GRAVADA como `pendente`. Nada é enviado neste passo;
  4. envio (E4.4): a outbox reivindica as pendentes, envia e marca a entrega.

OUTBOX (achado do 2º ciclo, L3): a linha nasce ANTES do envio, com chave idempotente
(um cartão por sinal). Antes, o Telegram era chamado primeiro e o registro vinha
depois — se o envio desse certo e o INSERT falhasse, o cartão era reenviado no ciclo
seguinte; e dois processos do L3 enviavam o mesmo cartão, porque nada os impedia.
Agora o envio é `pendente → enviando → entregue`, com reserva atômica.

Resta uma janela irredutível entre "o Telegram aceitou" e "o banco marcou entregue":
a API não oferece chave de idempotência em `sendMessage`. A escolha é deliberada —
**duplicata rara é preferível a sinal perdido em silêncio.**

Nota de contrato (PC-EXPIRA): `expirado` como STATUS só é alcançável de
`aguardando_crivo` (schema 0001). No passo 3 o sinal JÁ é `confirmado` (imutável):
a expiração no envio é SUPRESSÃO do cartão + registro interno, não muda o status.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from sinalizador.comum.erros import e_violacao_unicidade
from sinalizador.comum.tempo import para_datetime

from .bot import Bot
from .cartao import formatar_cartao, janela_fechou, odd_atual, preco_caiu

_log = logging.getLogger(__name__)

DAEMON = "l3"


@dataclass
class ResumoL3:
    expirados: int = 0          # aguardando_crivo → expirado (frescor)
    enfileirados: int = 0       # cartões GRAVADOS como pendentes (ainda não enviados)
    enviados: int = 0           # cartões com entrega CONFIRMADA (nunca as falhas)
    falhas_envio: int = 0       # devolvidos à fila para o próximo ciclo
    suprimidos: int = 0         # confirmados cuja janela fechou
    alertas_entregues: int = 0  # entregas que não são cartão de sinal


def _odd_venue_atual(banco: Any, sinal: dict[str, Any], *,
                     agora: Optional[datetime], idade_max_s: Optional[float]) -> Any:
    snap = banco.ultimo_snapshot_venue(
        sinal["evento_id"], sinal["casa_venue_id"], sinal["mercado"],
        sinal["selecao"], sinal.get("linha"),
    )
    # A idade é conferida aqui: o ÚLTIMO snapshot não é necessariamente um snapshot
    # ATUAL — sem isto, um preço de dias atrás valia como corrente.
    return odd_atual(snap, agora=agora, idade_max_s=idade_max_s)


def expirar_pendentes(banco: Any, *, limite: int = 200,
                      agora: Optional[datetime] = None,
                      idade_max_s: Optional[float] = None) -> int:
    """E4.2 (frescor): expira sinais `aguardando_crivo` cujo preço COMPROVADAMENTE
    caiu abaixo da mínima. Ausência — ou VELHICE — de preço não expira: preço velho
    não prova movimento (não se inventa queda)."""
    n = 0
    for sinal in banco.sinais_aguardando_crivo(limite):
        odd = _odd_venue_atual(banco, sinal, agora=agora, idade_max_s=idade_max_s)
        if preco_caiu(odd, float(sinal["odd_minima_aceitavel"])):
            try:
                banco.transicionar_status_sinal(sinal["id"], "expirado")
                n += 1
                _log.info("sinal expirado por frescor de preço",
                          extra={"sinal_id": sinal["id"], "odd_atual": odd})
            except Exception:  # corrida com o L2 (confirmou primeiro) — ok, não é erro
                _log.info("expiração perdeu a corrida para o L2",
                          extra={"sinal_id": sinal["id"]})
    return n


def alerta_drawdown(banco: Any) -> bool:
    """E4.3: enfileira um alerta de drawdown se o kill switch disparou e não há
    outro alerta_drawdown pendente (anti-spam)."""
    banca = banco.banca_atual()
    if not banca or not banca.get("kill_switch"):
        return False
    if any(nt.get("tipo") == "alerta_drawdown" for nt in banco.notificacoes_pendentes(500)):
        return False
    banco.inserir("notificacoes", {
        "sinal_id": None, "tipo": "alerta_drawdown", "canal": "telegram",
        "conteudo": (f"⛔ KILL SWITCH — drawdown {banca.get('drawdown_pct')}% atingiu o limite (P9). "
                     f"Emissão de sinais suspensa até revisão formal (Seção 7)."),
    })
    _log.warning("kill switch de drawdown — alerta enfileirado",
                 extra={"drawdown_pct": banca.get("drawdown_pct")})
    return True


def enfileirar_cartoes(banco: Any, *, limite: int = 200,
                       agora: Optional[datetime] = None,
                       idade_max_s: Optional[float] = None) -> ResumoL3:
    """E4.1/E4.2: grava como `pendente` o cartão de cada confirmado ainda sem cartão.

    NÃO ENVIA NADA: registrar antes de enviar é o que impede o cartão duplicado.
    O envio é do passo 4, pela outbox.
    """
    resumo = ResumoL3()
    for sinal in banco.sinais_por_status("confirmado", limite):
        if banco.notificacoes_do_sinal(sinal["id"], tipo="sinal"):
            continue  # já tem cartão (enviado ou na fila) — caminho rápido
        odd = _odd_venue_atual(banco, sinal, agora=agora, idade_max_s=idade_max_s)
        minima = float(sinal["odd_minima_aceitavel"])
        if janela_fechou(odd, minima):
            # Janela fechada (preço caiu, ausente OU velho) → não vira cartão. O sinal
            # já é 'confirmado' (imutável): fica o registro INTERNO, que não vai ao bot.
            banco.inserir("notificacoes", {
                "sinal_id": sinal["id"], "tipo": "administrativo", "canal": "telegram",
                "conteudo": (f"[expirado-no-envio] sinal {sinal['id']}: "
                             f"odd atual {odd} < mínima {minima} — cartão suprimido"),
                "status": "interno",
            })
            resumo.suprimidos += 1
            _log.info("cartão suprimido (janela fechou)",
                      extra={"sinal_id": sinal["id"], "odd_atual": odd})
            continue
        crivo = banco.crivo_do_sinal(sinal["id"])
        evento = banco.evento_por_id(sinal["evento_id"])
        texto = formatar_cartao(sinal, crivo, evento, odd_atual_venue=odd)
        try:
            banco.inserir("notificacoes", {
                "sinal_id": sinal["id"], "tipo": "sinal", "canal": "telegram",
                "conteudo": texto, "status": "pendente",
            })
        except Exception as e:
            # `ux_notificacao_cartao`: outro processo enfileirou o mesmo cartão.
            # É o resultado desejado (existe UM cartão), não erro.
            if e_violacao_unicidade(e):
                _log.info("cartão já enfileirado por outro processo (corrida)",
                          extra={"sinal_id": sinal["id"]})
                continue
            raise
        resumo.enfileirados += 1
    return resumo


def entregar_pendentes(banco: Any, bot: Bot, *, limite: int = 200,
                       agora_iso: Optional[str] = None,
                       reclaim_s: float = 300.0) -> tuple[int, int, int]:
    """E4.3/E4.4 — o REMETENTE da outbox. Devolve (entregues, falhas, cartoes).

    Reivindica atomicamente (`pendente → enviando`), envia e só então marca
    `entregue`. Falha de envio devolve a linha à fila na hora. O contador conta
    ENTREGA CONFIRMADA — antes ele somava mesmo quando `bot.enviar()` devolvia
    False, e o resumo dizia "enviado" para mensagem que nunca saiu.
    """
    entregues = falhas = cartoes = 0
    for notif in banco.reivindicar_notificacoes(agora_iso, limite, reclaim_s):
        if bot.enviar(notif["conteudo"]):
            banco.marcar_notificacao_entregue(notif["id"], agora_iso)
            entregues += 1
            if notif.get("tipo") == "sinal":
                cartoes += 1
        else:
            banco.devolver_notificacao(notif["id"])
            falhas += 1
            _log.warning("envio falhou — devolvido à fila para o próximo ciclo",
                         extra={"notificacao_id": notif["id"], "tipo": notif.get("tipo")})
    return entregues, falhas, cartoes


def processar(banco: Any, bot: Bot, gates: Any, *, limite: int = 200,
              agora_iso: Optional[str] = None, reclaim_s: float = 300.0) -> ResumoL3:
    """Um ciclo completo do L3, na ordem do cabeçalho. Pulsa o heartbeat `l3`."""
    agora = para_datetime(agora_iso)
    idade_max_s = float(gates.get("snapshot_idade_max_s"))

    # 1. frescor ANTES de emitir (a ordem que a documentação sempre descreveu).
    expirados = expirar_pendentes(banco, limite=limite, agora=agora,
                                  idade_max_s=idade_max_s)
    # 2. alerta de drawdown.
    alerta_drawdown(banco)
    # 3. enfileira os cartões (grava, não envia).
    resumo = enfileirar_cartoes(banco, limite=limite, agora=agora,
                                idade_max_s=idade_max_s)
    resumo.expirados = expirados
    # 4. envia o que está na fila (cartões + alertas), pela outbox.
    entregues, falhas, cartoes = entregar_pendentes(
        banco, bot, limite=limite, agora_iso=agora_iso, reclaim_s=reclaim_s)
    resumo.enviados = cartoes
    resumo.falhas_envio = falhas
    resumo.alertas_entregues = entregues - cartoes

    detalhe = {"enviados": resumo.enviados, "enfileirados": resumo.enfileirados,
               "falhas_envio": resumo.falhas_envio, "suprimidos": resumo.suprimidos,
               "expirados": resumo.expirados, "alertas": resumo.alertas_entregues}
    banco.pulsar(DAEMON, detalhe)
    _log.info("ciclo L3 concluído", extra=detalhe)
    return resumo
