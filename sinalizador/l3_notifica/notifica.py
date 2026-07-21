"""L3 — orquestração da notificação. Sem rede/token no núcleo (Bot injetável).

Um ciclo (`processar`) faz, nesta ordem:
  1. varredura de frescor (E4.2): sinais `aguardando_crivo` cujo preço já caiu
     abaixo da mínima viram `expirado` — a ÚNICA forma de `expirado` (o trigger só
     permite transição a partir de `aguardando_crivo`) e ainda poupa o L2 de
     avaliar sinal morto;
  2. alerta de drawdown (E4.3): se o kill switch da banca disparou, enfileira um
     alerta (sem spam);
  3. emissão dos confirmados (E4.1): para cada `confirmado` ainda não notificado,
     re-checa o preço NO ENVIO — se a janela fechou, SUPRIME o cartão (E4.2) e
     registra a supressão; senão monta o cartão e envia;
  4. entrega dos alertas pendentes (E4.3/E4.4): daemon mudo, drawdown, erro do L2.

Nota de contrato (PC-EXPIRA): `expirado` como STATUS só é alcançável de
`aguardando_crivo` (schema 0001). No passo 3 o sinal JÁ é `confirmado` (imutável):
a expiração no envio é SUPRESSÃO do cartão + registro administrativo, não muda o
status. A regra de entrega é: `notificacoes.entregue=false` = a enviar; `true` =
já enviado OU registro interno (não vai ao bot).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .bot import Bot
from .cartao import formatar_cartao, janela_fechou, odd_atual, preco_caiu

_log = logging.getLogger(__name__)

DAEMON = "l3"


@dataclass
class ResumoL3:
    expirados: int = 0          # aguardando_crivo → expirado (frescor)
    enviados: int = 0           # cartões de sinal enviados
    suprimidos: int = 0         # confirmados cuja janela fechou no envio
    alertas_entregues: int = 0  # alertas pendentes entregues


def _odd_venue_atual(banco: Any, sinal: dict[str, Any]) -> Any:
    snap = banco.ultimo_snapshot_venue(
        sinal["evento_id"], sinal["casa_venue_id"], sinal["mercado"],
        sinal["selecao"], sinal.get("linha"),
    )
    return odd_atual(snap)


def expirar_pendentes(banco: Any, *, limite: int = 200) -> int:
    """E4.2 (frescor): expira sinais `aguardando_crivo` cujo preço COMPROVADAMENTE
    caiu abaixo da mínima. Ausência de preço não expira (não se inventa movimento)."""
    n = 0
    for sinal in banco.sinais_aguardando_crivo(limite):
        odd = _odd_venue_atual(banco, sinal)
        if preco_caiu(odd, float(sinal["odd_minima_aceitavel"])):
            try:
                banco.transicionar_status_sinal(sinal["id"], "expirado")
                n += 1
                _log.info("sinal expirado por frescor de preço", extra={"sinal_id": sinal["id"], "odd_atual": odd})
            except Exception:  # corrida com o L2 (confirmou primeiro) — ok, não é erro
                _log.info("expiração perdeu a corrida para o L2", extra={"sinal_id": sinal["id"]})
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
        "entregue": False,
    })
    _log.warning("kill switch de drawdown — alerta enfileirado", extra={"drawdown_pct": banca.get("drawdown_pct")})
    return True


def emitir_confirmados(banco: Any, bot: Bot, *, limite: int = 200) -> ResumoL3:
    """E4.1/E4.2/E4.4: emite os cartões dos confirmados ainda não notificados,
    re-checando o preço no envio."""
    resumo = ResumoL3()
    for sinal in banco.sinais_por_status("confirmado", limite):
        if banco.notificacoes_do_sinal(sinal["id"], tipo="sinal"):
            continue  # já notificado — não reenvia
        odd = _odd_venue_atual(banco, sinal)
        minima = float(sinal["odd_minima_aceitavel"])
        if janela_fechou(odd, minima):
            # E4.2: janela fechada no envio → NÃO notifica. O sinal já é 'confirmado'
            # (imutável): registra a supressão como administrativo interno (entregue=true).
            banco.inserir("notificacoes", {
                "sinal_id": sinal["id"], "tipo": "administrativo", "canal": "telegram",
                "conteudo": f"[expirado-no-envio] sinal {sinal['id']}: odd atual {odd} < mínima {minima} — cartão suprimido",
                "entregue": True,
            })
            resumo.suprimidos += 1
            _log.info("cartão suprimido (janela fechou no envio)", extra={"sinal_id": sinal["id"], "odd_atual": odd})
            continue
        crivo = banco.crivo_do_sinal(sinal["id"])
        evento = banco.evento_por_id(sinal["evento_id"])
        texto = formatar_cartao(sinal, crivo, evento, odd_atual_venue=odd)
        enviado = bool(bot.enviar(texto))
        banco.inserir("notificacoes", {
            "sinal_id": sinal["id"], "tipo": "sinal", "canal": "telegram",
            "conteudo": texto, "entregue": enviado,
        })
        resumo.enviados += 1
    return resumo


def entregar_pendentes(banco: Any, bot: Bot, *, limite: int = 200) -> int:
    """E4.3/E4.4: entrega os alertas pendentes (entregue=false) — daemon mudo,
    drawdown, erro do L2. Falha de envio deixa pendente (retry no próximo ciclo)."""
    n = 0
    for notif in banco.notificacoes_pendentes(limite):
        if bot.enviar(notif["conteudo"]):
            banco.marcar_notificacao_entregue(notif["id"])
            n += 1
    return n


def processar(banco: Any, bot: Bot, *, limite: int = 200) -> ResumoL3:
    """Um ciclo completo do L3. Pulsa o heartbeat `l3`."""
    resumo = emitir_confirmados(banco, bot, limite=limite)
    resumo.expirados = expirar_pendentes(banco, limite=limite)
    alerta_drawdown(banco)
    resumo.alertas_entregues = entregar_pendentes(banco, bot, limite=limite)
    banco.pulsar(DAEMON, {"enviados": resumo.enviados, "suprimidos": resumo.suprimidos,
                          "expirados": resumo.expirados, "alertas": resumo.alertas_entregues})
    _log.info("ciclo L3 concluído", extra={"enviados": resumo.enviados, "suprimidos": resumo.suprimidos,
                                           "expirados": resumo.expirados, "alertas": resumo.alertas_entregues})
    return resumo
