"""E5.5 — relatório diário (texto para o Telegram): CLV, banca, saúde dos daemons.

Puro: recebe as linhas das views e devolve texto. Honestidade estatística (P12):
abaixo de 200 sinais, o CLV vem com aviso de amostra insuficiente.
"""
from __future__ import annotations

from typing import Any, Optional

AMOSTRA_MINIMA = 200


def _num(v: Any, casas: int = 2) -> str:
    try:
        return f"{float(v):.{casas}f}"
    except (TypeError, ValueError):
        return "—"


def _linha_clv(linhas: list[dict[str, Any]], contrafactual: bool) -> Optional[dict[str, Any]]:
    for r in linhas:
        if bool(r.get("contrafactual")) is contrafactual:
            return r
    return None


def formatar_relatorio(
    clv_global: list[dict[str, Any]],
    banca: Optional[dict[str, Any]],
    saude_daemons: list[dict[str, Any]],
    *,
    limiar_silencio_s: float = 3600.0,
) -> str:
    real = _linha_clv(clv_global, contrafactual=False)
    contra = _linha_clv(clv_global, contrafactual=True)
    n_real = int(real["n"]) if real and real.get("n") is not None else 0

    linhas = ["📊 RELATÓRIO — Sinalizador"]

    if real:
        aviso = "  ⚠️ amostra < 200 (ruído, P12)" if n_real < AMOSTRA_MINIMA else ""
        linhas.append(f"CLV real (confirmados): {_num(real.get('clv_medio'), 3)}%"
                      f" · n={n_real}{aviso}")
    else:
        linhas.append("CLV real: sem dados ainda")
    if contra:
        linhas.append(f"CLV contrafactual (vetados/abortos): {_num(contra.get('clv_medio'), 3)}%"
                      f" · n={int(contra.get('n') or 0)}")

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
