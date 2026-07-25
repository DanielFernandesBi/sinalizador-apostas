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
  4. envio (E4.4): a outbox reivindica as pendentes, envia e marca a entrega. O
     cartão ENTREGUE abre a posição de papel (Sugestão nº 13 / P0.8): é aqui, e só
     aqui, que a oportunidade passa a consumir os tetos de exposição — o análogo
     exato do momento em que o dinheiro real sairia da banca.

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
    posicoes_papel: int = 0     # reservas de exposição abertas pela entrega (P0.8)
    mortas: int = 0             # P1.6: a outbox desistiu (dead-letter)


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


def alerta_drawdown(banco: Any, *, agora_iso: Optional[str] = None) -> bool:
    """E4.3: UM alerta por EPISÓDIO de suspensão (P1.4).

    O anti-spam antigo olhava só notificações ainda PENDENTES — depois de entregue, a
    linha some da fila e o ciclo seguinte enfileirava outro alerta. Como o kill switch
    fica ligado até revisão formal (§7), era um alerta por ciclo do L3, para sempre. A
    outbox (0010) piorou: antes `entregue` era booleano e a linha continuava visível.

    Agora a unidade é o episódio, e quem garante "um só" é o banco (índice parcial de
    episódio aberto), não a sorte de a notificação anterior ainda estar na fila.
    """
    banca = banco.banca_atual()
    abrir = getattr(banco, "abrir_episodio_kill_switch", None)
    encerrar = getattr(banco, "encerrar_episodio_kill_switch", None)

    if not banca or not banca.get("kill_switch"):
        # Saiu da suspensão: fecha o episódio para que a PRÓXIMA volte a alertar.
        if encerrar is not None:
            encerrar("kill switch desarmado", agora_iso)
        return False

    if abrir is None:                                  # banco sem a RPC (fake antigo)
        if any(nt.get("tipo") == "alerta_drawdown" for nt in banco.notificacoes_pendentes(500)):
            return False
    else:
        r = abrir(banca.get("pico"), banca.get("drawdown_pct"), agora_iso)
        if not r.get("abriu"):
            return False                               # a MESMA suspensão já alertou
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
                       idade_max_s: Optional[float] = None,
                       agora_iso: Optional[str] = None) -> ResumoL3:
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
            # já é 'confirmado' (imutável). P1.3: isto CONTA como tentativa numa linha
            # única por sinal — antes inseria uma notificação administrativa por ciclo,
            # centenas por sinal. E não sela desfecho: o preço pode voltar acima da
            # mínima antes do apito, e aí o cartão sai.
            motivo = ("preco_abaixo_da_minima" if odd is not None
                      else "frescor_sem_preco_atual")
            registrar = getattr(banco, "registrar_tentativa_cartao", None)
            if registrar is not None:
                registrar(sinal["id"], motivo, agora_iso)
            resumo.suprimidos += 1
            _log.info("cartão não enviado neste ciclo (janela fechada)",
                      extra={"sinal_id": sinal["id"], "odd_atual": odd, "motivo": motivo})
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


def _reservar_papel(banco: Any, sinal_id: Any, *, agora_iso: Optional[str]) -> bool:
    """P0.8 — a oportunidade ENTREGUE abre a posição de papel.

    É o análogo exato de "o Daniel apostou": só depois disto o stake nocional passa a
    consumir os tetos de exposição. O banco decide se cabe reservar (regime de papel,
    antes do apito, ainda sem posição) — aqui não há regra, só a chamada. Falha de
    rede não pode desfazer uma entrega já confirmada: registra e segue (o sinal volta
    a contar como "em voo" na `vw_exposicao_total`, que é o lado seguro do erro).
    """
    reservar = getattr(banco, "reservar_exposicao_papel", None)
    if reservar is None or sinal_id is None:
        return False
    try:
        r = reservar(sinal_id, agora_iso)
    except Exception:
        _log.warning("falha ao reservar exposição de papel — cartão JÁ entregue",
                     extra={"sinal_id": sinal_id}, exc_info=True)
        return False
    if isinstance(r, dict) and r.get("reservado"):
        return True
    motivo = r.get("motivo") if isinstance(r, dict) else None
    if motivo not in (None, "ja_reservado", "regime_real"):
        _log.warning("exposição de papel não reservada",
                     extra={"sinal_id": sinal_id, "motivo": motivo})
    return False


def entregar_pendentes(banco: Any, bot: Bot, *, limite: int = 200,
                       agora_iso: Optional[str] = None,
                       reclaim_s: float = 300.0) -> tuple[int, int, int, int, int]:
    """E4.3/E4.4 — o REMETENTE. Devolve (entregues, falhas, cartoes, reservas, mortas).

    Reivindica atomicamente (`pendente → enviando`), envia e só então marca
    `entregue`. Falha de envio devolve a linha à fila na hora. O contador conta
    ENTREGA CONFIRMADA — antes ele somava mesmo quando `bot.enviar()` devolvia
    False, e o resumo dizia "enviado" para mensagem que nunca saiu.
    """
    entregues = falhas = cartoes = reservas = mortas = 0
    for notif in banco.reivindicar_notificacoes(agora_iso, limite, reclaim_s):
        if bot.enviar(notif["conteudo"]):
            banco.marcar_notificacao_entregue(notif["id"], agora_iso)
            entregues += 1
            if notif.get("tipo") == "sinal":
                cartoes += 1
                # P1.3: sela o desfecho de entrega — é o que o L4 lê para separar CLV
                # real de perda de mercado e de perda operacional (P0.7).
                selar = getattr(banco, "registrar_entrega_cartao", None)
                if selar is not None and notif.get("sinal_id"):
                    selar(notif["sinal_id"], agora_iso)
                if _reservar_papel(banco, notif.get("sinal_id"), agora_iso=agora_iso):
                    reservas += 1
        else:
            # P1.6: a devolução agora tem backoff e teto — antes voltava a `pendente`
            # na hora, então token revogado girava para sempre disputando a fila.
            r = banco.devolver_notificacao(notif["id"], "bot.enviar() devolveu False",
                                           agora_iso)
            falhas += 1
            if isinstance(r, dict) and r.get("morta"):
                mortas += 1
                _log.error("outbox DESISTIU da notificação (dead-letter)",
                           extra={"notificacao_id": notif["id"], "tipo": notif.get("tipo"),
                                  "tentativas": r.get("tentativas")})
            else:
                _log.warning("envio falhou — volta à fila após o backoff",
                             extra={"notificacao_id": notif["id"], "tipo": notif.get("tipo"),
                                    "espera_s": (r or {}).get("espera_s")})
    return entregues, falhas, cartoes, reservas, mortas


def processar(banco: Any, bot: Bot, gates: Any, *, limite: int = 200,
              agora_iso: Optional[str] = None, reclaim_s: float = 300.0) -> ResumoL3:
    """Um ciclo completo do L3, na ordem do cabeçalho. Pulsa o heartbeat `l3`."""
    agora = para_datetime(agora_iso)
    idade_max_s = float(gates.get("snapshot_idade_max_s"))

    # 1. frescor ANTES de emitir (a ordem que a documentação sempre descreveu).
    expirados = expirar_pendentes(banco, limite=limite, agora=agora,
                                  idade_max_s=idade_max_s)
    # 2. alerta de drawdown.
    alerta_drawdown(banco, agora_iso=agora_iso)
    # 3. enfileira os cartões (grava, não envia).
    resumo = enfileirar_cartoes(banco, limite=limite, agora=agora,
                                idade_max_s=idade_max_s, agora_iso=agora_iso)
    resumo.expirados = expirados
    # 4. envia o que está na fila (cartões + alertas), pela outbox — e a entrega
    #    confirmada abre a posição de papel (P0.8).
    entregues, falhas, cartoes, reservas, mortas = entregar_pendentes(
        banco, bot, limite=limite, agora_iso=agora_iso, reclaim_s=reclaim_s)
    resumo.enviados = cartoes
    resumo.falhas_envio = falhas
    resumo.alertas_entregues = entregues - cartoes
    resumo.posicoes_papel = reservas
    resumo.mortas = mortas

    detalhe = {"enviados": resumo.enviados, "enfileirados": resumo.enfileirados,
               "falhas_envio": resumo.falhas_envio, "suprimidos": resumo.suprimidos,
               "expirados": resumo.expirados, "alertas": resumo.alertas_entregues,
               "posicoes_papel": resumo.posicoes_papel, "mortas": resumo.mortas}
    banco.pulsar(DAEMON, detalhe)
    _log.info("ciclo L3 concluído", extra=detalhe)
    return resumo
