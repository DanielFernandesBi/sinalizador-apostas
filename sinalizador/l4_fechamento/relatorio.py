"""E5.5 — relatório diário (texto para o Telegram): CLV, banca, saúde dos daemons.

Puro: recebe as linhas das views e devolve texto. Honestidade estatística (P12):
abaixo da amostra mínima, o CLV vem com aviso de ruído.

DUAS CORREÇÕES DO TIER 3 (2º ciclo):

  1. O relatório dizia "diário" e mostrava o ACUMULADO GLOBAL desde o início. Um dia
     ruim ficava invisível dentro da média histórica. Agora há a seção do DIA (as
     partidas daquela data) e, abaixo, o acumulado — que continua sendo o que decide
     homologação, mas deixa de ser a única coisa visível.

  2. As categorias NÃO se somam. Depois da Sugestão nº 10, `contrafactual_l2` (veto
     do crivo), `contrafactual_l1` (near-miss) e `contrafactual_operacional`
     (expirado/erro/timeout) são coisas distintas — misturá-las numa média só, como
     a linha "CLV contrafactual" fazia, não responde nenhuma das perguntas que cada
     uma responde. Cada categoria tem sua linha.

`amostra_minima` vem do GATE homônimo (pétreo, Doutrina §4), nunca de constante:
era o último número de governança ainda cravado em código (regra 6).
"""
from __future__ import annotations

from typing import Any, Optional

# Rótulos legíveis por categoria de desfecho (Doutrina §3 / Sugestão nº 10).
ROTULO_CATEGORIA: dict[str, str] = {
    "real": "CLV real (confirmados)",
    "contrafactual_l2": "CLV contrafactual — vetados pelo crivo",
    "contrafactual_l1": "CLV contrafactual — near-miss do L1",
    "contrafactual_operacional": "CLV contrafactual — perdas operacionais",
    "calibracao": "CLV de calibração — mercados não homologados",
}
# Ordem de exibição: o real primeiro (é o KPI), depois o que explica o processo.
ORDEM_CATEGORIA = ("real", "contrafactual_l2", "contrafactual_l1",
                   "contrafactual_operacional", "calibracao")

ROTULO_MOTIVO: dict[str, str] = {
    "indisponivel_sem_referencia": "sem referência",
    "indisponivel_sem_revisao_completa": "sem book completo",
    "indisponivel_revisao_defasada": "book defasado",
    "indisponivel_evento_inconsistente": "dado inconsistente",
}


def _num(v: Any, casas: int = 2) -> str:
    try:
        return f"{float(v):.{casas}f}"
    except (TypeError, ValueError):
        return "—"


def _linhas_clv(linhas: list[dict[str, Any]], *, amostra_minima: int,
                indentar: bool = False) -> list[str]:
    """Uma linha por categoria — nunca uma média que some categorias diferentes."""
    por_cat = {r.get("categoria"): r for r in linhas}
    saida: list[str] = []
    pre = "  " if indentar else ""
    for cat in ORDEM_CATEGORIA:
        r = por_cat.get(cat)
        if not r:
            continue
        n = int(r.get("n") or 0)
        # O aviso de ruído vale para o KPI soberano; nas demais categorias a amostra
        # é diagnóstica, não decisória.
        aviso = (f"  ⚠️ amostra < {amostra_minima} (ruído, P12)"
                 if cat == "real" and n < amostra_minima else "")
        saida.append(f"{pre}{ROTULO_CATEGORIA[cat]}: {_num(r.get('clv_medio'), 3)}%"
                     f" · n={n}{aviso}")
    return saida


def _linha_perda(perda: list[dict[str, Any]], *, indentar: bool = False) -> Optional[str]:
    """Amostra que NÃO virou CLV, por motivo. Sumir em silêncio seria pior: a ausência
    se concentra em mercados voláteis e enviesaria a série (Sugestão nº 10)."""
    if not perda:
        return None
    total = sum(int(r.get("n") or 0) for r in perda)
    if not total:
        return None
    por_motivo: dict[str, int] = {}
    for r in perda:
        rot = ROTULO_MOTIVO.get(r.get("motivo"), str(r.get("motivo")))
        por_motivo[rot] = por_motivo.get(rot, 0) + int(r.get("n") or 0)
    detalhe = " · ".join(f"{k}: {v}" for k, v in sorted(por_motivo.items()))
    pre = "  " if indentar else ""
    return f"{pre}Sem CLV: {total} ({detalhe})"


def formatar_relatorio(
    clv_acumulado: list[dict[str, Any]],
    banca: Optional[dict[str, Any]],
    saude_daemons: list[dict[str, Any]],
    *,
    amostra_minima: int,
    dia: Optional[str] = None,
    clv_do_dia: Optional[list[dict[str, Any]]] = None,
    perda_do_dia: Optional[list[dict[str, Any]]] = None,
    limiar_silencio_s: float = 3600.0,
) -> str:
    """Monta o texto. `clv_acumulado`/`clv_do_dia` vêm de `vw_clv_por_categoria` e
    `vw_clv_diario`; `perda_do_dia`, de `vw_clv_perda_amostra`."""
    titulo = "📊 RELATÓRIO — Sinalizador"
    if dia:
        titulo += f" · {dia}"
    linhas = [titulo]

    # --- o DIA ---
    if dia is not None:
        linhas.append("")
        linhas.append(f"HOJE ({dia}, partidas do dia)")
        do_dia = _linhas_clv(clv_do_dia or [], amostra_minima=amostra_minima, indentar=True)
        linhas.extend(do_dia or ["  sem CLV fechado no dia"])
        perda = _linha_perda(perda_do_dia or [], indentar=True)
        if perda:
            linhas.append(perda)

    # --- ACUMULADO (o que decide homologação) ---
    linhas.append("")
    linhas.append("ACUMULADO")
    acumulado = _linhas_clv(clv_acumulado, amostra_minima=amostra_minima, indentar=True)
    linhas.extend(acumulado or ["  sem dados ainda"])

    linhas.append("")
    if banca:
        kill = " · ⛔ KILL SWITCH" if banca.get("kill_switch") else ""
        linhas.append(f"Banca: {_num(banca.get('saldo'))} · pico {_num(banca.get('pico'))}"
                      f" · drawdown {_num(banca.get('drawdown_pct'))}%{kill}")
    else:
        linhas.append("Banca: sem ledger (modo papel/nominal)")

    mudos = [d.get("daemon") for d in saude_daemons
             if (d.get("segundos_em_silencio") or 0) > limiar_silencio_s]
    linhas.append(f"Daemons mudos: {mudos or 'nenhum'}")
    return "\n".join(linhas)
