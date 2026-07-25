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
