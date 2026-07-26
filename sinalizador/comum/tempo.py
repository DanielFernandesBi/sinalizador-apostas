"""Conversão de carimbo de tempo — SEM importar o SDK (usável pelos núcleos).

Uma implementação só para todas as camadas: ISO 8601 (com `Z` ou offset) →
`datetime` aware em UTC. `None` quando ausente/inválido — nunca chuta um horário,
porque um carimbo inventado vira sincronia falsa (L1) ou fechamento falso (L4).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional


def para_datetime(valor: Any) -> Optional[datetime]:
    """ISO 8601 → datetime aware (UTC). None se ausente/inválido."""
    if isinstance(valor, datetime):
        return valor if valor.tzinfo else valor.replace(tzinfo=timezone.utc)
    if not valor:
        return None
    try:
        dt = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# Tolerância de RELÓGIO entre a fonte e nós. NÃO é gate de aposta: é margem para o
# desencontro normal de relógios (a fonte carimba, a rede atrasa, nossos relógios
# divergem). Se algum dia precisar de calibração empírica, vira gate pelo rito.
TOLERANCIA_RELOGIO_S = 60.0


def idade_s(agora: Optional[datetime], ts: Optional[datetime], *,
            tolerancia_futuro_s: float = TOLERANCIA_RELOGIO_S) -> Optional[float]:
    """Idade do carimbo, em segundos. None quando não dá para afirmar frescor (P1.8).

    Duas devoluções `None`, ambas fail-closed:

      - carimbo ausente ou ilegível — não se chuta horário;
      - carimbo no FUTURO além da tolerância de relógio. Isso não é dado fresco, é
        dado INCONSISTENTE: a idade dá negativa e passa em QUALQUER teto de idade,
        então o preço mais suspeito do lote seria o que menos apanha. P6 manda
        abortar diante de inconsistência, não premiá-la.
    """
    if agora is None or ts is None:
        return None
    delta = (agora - ts).total_seconds()
    if delta < -abs(tolerancia_futuro_s):
        return None
    return max(delta, 0.0)
