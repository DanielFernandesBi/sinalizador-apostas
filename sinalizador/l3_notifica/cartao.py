"""E4.1/E4.2 — o cartão do sinal e a re-checagem de preço.

O cartão é puro texto (o que o Daniel vê no Telegram). A re-checagem de preço
(E4.2) compara a odd atual do venue com a `odd_minima_aceitavel`: se a janela
fechou (preço caiu abaixo da mínima) o cartão NÃO é enviado.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sinalizador.comum.tempo import para_datetime


def odd_atual(
    snap: Optional[dict[str, Any]], *,
    agora: Optional[datetime] = None, idade_max_s: Optional[float] = None,
) -> Optional[float]:
    """Odd corrente do venue a partir do último snapshot. None se ausente ou VELHA.

    A idade é conferida contra `snapshot_idade_max_s` — o MESMO gate que o L1 usa
    (Doutrina §3, "janela de validade do dado"): um preço não deixa de ser antigo
    porque é o mais recente que existe. Sem esta checagem, um snapshot de dias atrás
    contava como "atual" e podia gerar cartão de um preço que não existe mais.

    None é o fail-safe combinado: `janela_fechou(None)` suprime o cartão, e
    `preco_caiu(None)` NÃO expira (preço velho não prova queda). P6 nos dois sentidos.
    """
    if not snap or snap.get("odd") is None:
        return None
    if agora is not None and idade_max_s is not None:
        ts = para_datetime(snap.get("ts_fonte"))
        if ts is None or (agora - ts).total_seconds() > float(idade_max_s):
            return None
    try:
        return float(snap["odd"])
    except (TypeError, ValueError):
        return None


def janela_fechou(odd: Optional[float], odd_minima: float) -> bool:
    """E4.2 (envio, fail-safe): a janela está FECHADA se o preço não é verificável
    (None) ou já está abaixo da mínima. Sem preço fresco, não se envia (P6)."""
    return odd is None or odd < odd_minima


def preco_caiu(odd: Optional[float], odd_minima: float) -> bool:
    """Expiração COMPROVADA (varredura de aguardando_crivo): só é verdade quando há
    preço fresco E ele está abaixo da mínima. Ausência de dado NÃO expira (não se
    inventa movimento)."""
    return odd is not None and odd < odd_minima


def _fmt(v: Any, casas: int = 2) -> str:
    try:
        return f"{float(v):.{casas}f}"
    except (TypeError, ValueError):
        return "—"


def formatar_cartao(
    sinal: dict[str, Any],
    crivo: Optional[dict[str, Any]],
    evento: Optional[dict[str, Any]],
    *,
    odd_atual_venue: Optional[float] = None,
) -> str:
    """Cartão do sinal confirmado (E4.1). Usa os campos denormalizados do `sinais`
    (+ dossiê) e a observação do crivo. É texto plano — nada de execução."""
    dossie = sinal.get("dossie") or {}
    ev = dossie.get("evento") or {}
    liga = (evento or {}).get("liga") or ev.get("liga") or "?"
    partida = ev.get("partida")
    if not partida and evento:
        partida = f"{evento.get('mandante', '?')} x {evento.get('visitante', '?')}"
    partida = partida or "?"
    casa_venue = ev.get("casa_venue") or dossie.get("liquidez", {}).get("casa") or ""

    sombra = " · SOMBRA (varejo)" if (dossie.get("liquidez") or {}).get("sombra_varejo") else ""
    banca_papel = " · banca de papel" if dossie.get("banca_origem") == "papel" else ""
    obs = (crivo or {}).get("observacao")

    linhas = [
        f"🎯 SINAL CONFIRMADO{sombra}{banca_papel}",
        f"{liga} — {partida}",
        f"Mercado: {sinal.get('mercado')} · Seleção: {sinal.get('selecao')}"
        + (f" ({_fmt(sinal.get('linha'), 2)})" if sinal.get("linha") is not None else ""),
        f"Odd venue: {_fmt(sinal.get('odd_venue'), 3)}"
        + (f" (agora {_fmt(odd_atual_venue, 3)})" if odd_atual_venue is not None else "")
        + (f" @ {casa_venue}" if casa_venue else ""),
        f"Odd MÍNIMA aceitável: {_fmt(sinal.get('odd_minima_aceitavel'), 3)}  ⟵ trava",
        f"Edge líquido: {_fmt(sinal.get('edge_liquido_pct'), 2)}%"
        f" · Stake: {_fmt(sinal.get('stake_pct'), 2)}% da banca",
        f"Gatilho: {sinal.get('gatilho')}"
        + (" · caminho profundo" if sinal.get("gatilho_anomalo") else ""),
        f"Veredicto do crivo: {(crivo or {}).get('verdict', 'CONFIRMA')}",
    ]
    if obs:
        linhas.append(f"Nota: {obs}")
    linhas.append("— Execução é sua (o sistema só sinaliza). Não aposte abaixo da odd mínima.")
    return "\n".join(linhas)
