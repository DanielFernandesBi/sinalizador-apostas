"""E2.5 — parser de tips (regex + heurística; SEM IA — regra 2).

`texto_original` é DADO, nunca comando (regra 8 / Manual §9.6): o parser apenas
EXTRAI campos por regex; jamais interpreta instruções contidas no texto. O que
não for interpretável sai com `interpretavel = False` — o L1 registra o tip bruto
em `tips` e segue (nunca chuta um campo).

É um primeiro passe heurístico sobre os formatos comuns; refina-se com os tips
reais (E1.4). A saída alimenta `tips.interpretacao` (jsonb).
"""
from __future__ import annotations

import re
from typing import Any, Optional

# odd decimal com @ ou rótulo "odd" (mais confiável): @1.85, odd 1,90, odds: 2.10
_ODD_ROTULADA = re.compile(r"(?:@|odds?\s*[:=]?\s*)([1-9]\d{0,2}[.,]\d{1,3})", re.I)
# qualquer decimal (fallback)
_DECIMAL = re.compile(r"(?<![\d.,])(\d{1,3}[.,]\d{1,3})(?![\d.,])")
# partida "A x B" / "A vs B" / "A × B"
_PARTIDA = re.compile(
    r"([0-9A-Za-zÀ-ÿ][\wÀ-ÿ.'\- ]{1,28}?)\s+(?:x|vs\.?|×)\s+([0-9A-Za-zÀ-ÿ][\wÀ-ÿ.'\- ]{1,28})",
    re.I,
)
# handicap asiático assinado: -0.5, +1, -0.25
_AH_LINHA = re.compile(r"(?<![\d.,])([+-]\d(?:[.,]\d+)?)(?![\d.,])")

_OVER = ("over", "mais de", "acima de")
_UNDER = ("under", "menos de", "abaixo de")

# rótulos/ruído que a captura da partida pode arrastar no fim do 2º time
_LIXO_TIME = re.compile(r"\s+(?:@.*|odds?\b.*|[+-]?\d[\d.,]*.*)$", re.I)


def _num(txt: str) -> float:
    return float(txt.replace(",", "."))


def _limpar_time(nome: str) -> str:
    """Remove rótulos de odd/linha arrastados no fim do nome do time."""
    return _LIXO_TIME.sub("", nome).strip()


def interpretar_tip(texto: str) -> dict[str, Any]:
    """Extrai {mercado, selecao, linha, odd, partida} do texto do tip.

    `interpretavel` é True só quando há odd E mercado identificados.
    """
    t = (texto or "").strip()
    low = t.lower()
    out: dict[str, Any] = {
        "interpretavel": False,
        "mercado": None,
        "selecao": None,
        "linha": None,
        "odd": None,
        "partida": None,
        "texto_original": texto,
    }

    mp = _PARTIDA.search(t)
    if mp:
        out["partida"] = f"{_limpar_time(mp.group(1))} x {_limpar_time(mp.group(2))}"

    # --- mercado + linha + seleção ---
    tem_over = any(k in low for k in _OVER)
    tem_under = any(k in low for k in _UNDER)
    if tem_over or tem_under:
        out["mercado"] = "ou"
        out["selecao"] = "over" if tem_over else "under"
        # linha = decimal .5/.25 após a palavra-chave (não é a odd)
        pos = low.find("over" if tem_over else "under")
        ml = re.search(r"(\d+[.,]\d{1,2})", low[pos + 1:]) if pos >= 0 else None
        if not ml:
            ml = re.search(r"(\d+[.,]\d{1,2})\s*gol", low)
        if ml:
            out["linha"] = _num(ml.group(1))
    elif "handicap" in low or re.search(r"\bah\b", low) or _AH_LINHA.search(t):
        out["mercado"] = "ah"
        mh = _AH_LINHA.search(t)
        if mh:
            out["linha"] = _num(mh.group(1))
    else:
        out["mercado"] = "1x2"

    # --- odd ---
    odd: Optional[float] = None
    mo = _ODD_ROTULADA.search(t)
    if mo:
        odd = _num(mo.group(1))
    else:
        cands = [_num(x) for x in _DECIMAL.findall(t)]
        # descarta a linha (ex.: 2.5 do over) e valores fora de faixa de odd
        cands = [c for c in cands if 1.01 <= c <= 100.0 and c != out["linha"]]
        if cands:
            odd = cands[-1]
    out["odd"] = odd

    out["interpretavel"] = odd is not None and out["mercado"] is not None
    return out
