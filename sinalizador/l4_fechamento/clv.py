"""E5.1/E5.2 — linha de fechamento (Pinnacle de-vigada) e cômputo do CLV.

CLV de um sinal (Doutrina §3): a odd capturada na emissão vs a linha de fechamento
da referência sharp, em probabilidade. Aqui:

    p_fechamento = prob JUSTA da referência no fechamento (de-vig Shin de TODAS as
                   seleções do mercado no último snapshot antes do início);
    clv_pct      = (odd_emissao × p_fechamento − 1) × 100   ("bateu o fechamento?")
                   > 0 → a odd da emissão era melhor que a linha justa de fecho.

Mercado com book de referência incompleto no fechamento → sem de-vig → sem CLV
(P6: não se inventa). Sinais confirmados dão CLV real; vetados/abortos dão CLV
`contrafactual` (auditoria do crivo — vw_clv_por_veto).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from sinalizador.l1_gatilhos.devig import devig_shin
from sinalizador.l1_gatilhos.orquestrador import ORDEM_SELECAO

_log = logging.getLogger(__name__)

DAEMON = "l4"


def _linha_key(linha: Any) -> Optional[float]:
    if linha is None:
        return None
    try:
        return round(float(linha), 2)
    except (TypeError, ValueError):
        return None


def prob_implicita(odd: float) -> float:
    return 1.0 / float(odd)


def clv_pct(odd_emissao: float, p_fechamento: float) -> float:
    """> 0 se a odd da emissão bate a linha justa de fechamento."""
    return (float(odd_emissao) * float(p_fechamento) - 1.0) * 100.0


def probs_fechamento_por_mercado(
    snaps_ref: list[dict[str, Any]]
) -> dict[tuple, dict[str, float]]:
    """Do conjunto de snapshots da REFERÊNCIA (já filtrado até o início), monta
    {(mercado, linha): {selecao: p_justa}} de-vigando Shin cada mercado completo.

    Usa o ÚLTIMO snapshot por (mercado, linha, seleção) = a linha de fechamento.
    Mercado sem TODAS as seleções da ordem canônica é pulado (P6).
    """
    # último snapshot por (mercado, linha, selecao) — assume ordenado por ts_fonte asc.
    ultimo: dict[tuple, dict[str, float]] = {}
    for s in snaps_ref:
        if s.get("odd") is None:
            continue
        chave = (s["mercado"], _linha_key(s.get("linha")))
        ultimo.setdefault(chave, {})[s["selecao"]] = float(s["odd"])

    out: dict[tuple, dict[str, float]] = {}
    for (mercado, linha), odds_por_sel in ultimo.items():
        ordem = ORDEM_SELECAO.get(mercado)
        if ordem is None or any(sel not in odds_por_sel for sel in ordem):
            continue  # mercado fora do escopo ou book incompleto no fechamento (P6)
        try:
            probs, _z = devig_shin([odds_por_sel[sel] for sel in ordem])
        except ValueError:
            continue
        out[(mercado, linha)] = dict(zip(ordem, probs))
    return out


def _linha_clv(
    *, sinal_id: Optional[str], aborto_id: Optional[int], odd_emissao: float,
    p_emissao: float, p_fechamento: float, contrafactual: bool, ts_fechamento: str,
) -> dict[str, Any]:
    return {
        "sinal_id": sinal_id,
        "aborto_l1_id": aborto_id,
        "contrafactual": contrafactual,
        "odd_emissao": odd_emissao,
        "odd_fechamento_ref": round(1.0 / p_fechamento, 4),
        "p_emissao": p_emissao,
        "p_fechamento": round(p_fechamento, 6),
        "clv_pct": round(clv_pct(odd_emissao, p_fechamento), 3),
        "ts_fechamento": ts_fechamento,
    }


def fechar_evento(banco: Any, evento: dict[str, Any]) -> int:
    """Fecha o CLV de um evento já iniciado. Devolve quantas linhas de `clv_log`
    gravou. Marca o evento 'encerrado' ao fim (sai da fila do L4)."""
    inicio = evento.get("inicio_utc")
    if not inicio:
        return 0
    ref_ids = [c["id"] for c in banco.casas_ativas() if c.get("tipo") == "referencia"]
    if not ref_ids:
        _log.warning("sem casa de referência ativa — fechamento impossível (P6)")
        return 0
    snaps_ref = banco.snapshots_do_evento(evento["id"], casa_ids=ref_ids, ate_iso=inicio)
    fechamentos = probs_fechamento_por_mercado(snaps_ref)
    if not fechamentos:
        return 0

    sinal_ids_com_clv, aborto_ids_com_clv = banco.clv_ids_registrados(evento["id"])
    gravadas = 0

    for sinal in banco.sinais_do_evento(evento["id"], status=["confirmado", "vetado"]):
        if sinal["id"] in sinal_ids_com_clv:
            continue
        p = (fechamentos.get((sinal["mercado"], _linha_key(sinal.get("linha")))) or {}).get(sinal["selecao"])
        if p is None:
            continue
        banco.inserir("clv_log", _linha_clv(
            sinal_id=sinal["id"], aborto_id=None, odd_emissao=float(sinal["odd_venue"]),
            p_emissao=float(sinal["p_justa"]), p_fechamento=p,
            contrafactual=(sinal["status"] == "vetado"), ts_fechamento=inicio,
        ))
        gravadas += 1

    for aborto in banco.abortos_rastreados_do_evento(evento["id"]):
        if aborto["id"] in aborto_ids_com_clv:
            continue
        dp = aborto.get("dossie_parcial") or {}
        sel, odd_venue = dp.get("selecao"), dp.get("odd_venue")
        if sel is None or odd_venue is None:
            continue
        p = (fechamentos.get((dp.get("mercado"), _linha_key(dp.get("linha")))) or {}).get(sel)
        if p is None:
            continue
        banco.inserir("clv_log", _linha_clv(
            sinal_id=None, aborto_id=aborto["id"], odd_emissao=float(odd_venue),
            p_emissao=float(dp.get("p_justa") or prob_implicita(odd_venue)),
            p_fechamento=p, contrafactual=True, ts_fechamento=inicio,
        ))
        gravadas += 1

    banco.marcar_evento_encerrado(evento["id"])
    _log.info("evento fechado", extra={"evento_id": evento["id"], "clv_gravadas": gravadas})
    return gravadas


def rodar_fechamento(banco: Any, agora_iso: str, *, limite: int = 200) -> dict[str, int]:
    """Fecha todos os eventos já iniciados e ainda abertos. Pulsa heartbeat `l4`."""
    eventos = banco.eventos_iniciados_sem_status_final(agora_iso, limite)
    total_eventos = 0
    total_clv = 0
    for evento in eventos:
        n = fechar_evento(banco, evento)
        total_eventos += 1
        total_clv += n
    banco.pulsar(DAEMON, {"eventos_fechados": total_eventos, "clv_gravadas": total_clv})
    _log.info("ciclo L4 concluído", extra={"eventos": total_eventos, "clv": total_clv})
    return {"eventos": total_eventos, "clv": total_clv}
