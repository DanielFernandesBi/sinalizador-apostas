"""Cadência adaptativa do L0 por proximidade do jogo — economia de créditos.

A The Odds API cobra por chamada de ODDS (mercados × regiões). No tier gratuito
(500/mês ≈ 16/dia) a cadência fixa é inviável: 60 min × 6 ligas já estoura. A
cadência adaptativa gasta crédito só quando há jogo perto e só nas ligas que têm
jogo — cortando o consumo em ordem de grandeza. É com o consumo REAL dela (logado
via `x-requests-*`) que o rito dimensiona o tier pago.

Política (tudo em `CadenciaConfig`, calibrável):
  - intervalo entre ciclos por PROXIMIDADE do jogo mais próximo:
      última hora (≤ 1h)      → 5 min
      pré-jogo   (≤ 6h)       → 10 min
      caso contrário           → 60 min (base)
  - liga (sport) só é consultada se tiver jogo em D+2 (≤ 48h). Liga sem jogo à
    vista não gasta crédito nenhum.

O calendário (QUANDO cada jogo começa) vem do endpoint `/events`, que NÃO consome
cota — então saber a proximidade é de graça. Só as chamadas de ODDS das ligas
ativas gastam.

Este módulo é PURO e injetável: recebe kickoffs e `agora`, decide intervalo e
ligas. O `cli` amarra o relógio real e a The Odds API (borda impura).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CadenciaConfig:
    base_s: float = 3600.0            # sem jogo próximo
    pre_jogo_s: float = 600.0         # dentro da janela de pré-jogo (10 min)
    ultima_hora_s: float = 300.0      # dentro da última hora (5 min)
    janela_pre_jogo_s: float = 6 * 3600.0
    janela_ultima_hora_s: float = 3600.0
    horizonte_poll_s: float = 48 * 3600.0   # D+2: liga sem jogo até aqui não é consultada


def parse_kickoff(valor: Any) -> Optional[datetime]:
    """`commence_time` ISO → datetime aware (UTC). None se ausente/inválido (P6)."""
    if isinstance(valor, datetime):
        return valor if valor.tzinfo else valor.replace(tzinfo=timezone.utc)
    if not valor:
        return None
    try:
        dt = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _proximidade_min_s(kickoffs: list[datetime], agora: datetime) -> Optional[float]:
    """Menor tempo (s) até um kickoff FUTURO. None se não há jogo futuro."""
    futuros = [(k - agora).total_seconds() for k in kickoffs if k > agora]
    return min(futuros) if futuros else None


def intervalo_s(kickoffs: list[datetime], agora: datetime, cfg: CadenciaConfig = CadenciaConfig()) -> float:
    """Intervalo até o próximo ciclo, pela proximidade do jogo mais próximo."""
    prox = _proximidade_min_s(kickoffs, agora)
    if prox is None:
        return cfg.base_s
    if prox <= cfg.janela_ultima_hora_s:
        return cfg.ultima_hora_s
    if prox <= cfg.janela_pre_jogo_s:
        return cfg.pre_jogo_s
    return cfg.base_s


def sport_ativo(kickoffs: list[datetime], agora: datetime, cfg: CadenciaConfig = CadenciaConfig()) -> bool:
    """A liga tem jogo em D+2? (senão, não se gasta crédito com ela)."""
    prox = _proximidade_min_s(kickoffs, agora)
    return prox is not None and prox <= cfg.horizonte_poll_s


@dataclass(frozen=True)
class PlanoCiclo:
    intervalo_s: float
    sports: tuple[str, ...]                 # ligas a consultar neste ciclo (odds)
    proximidade_min_s: Optional[float] = None
    kickoffs_por_sport: dict[str, int] = field(default_factory=dict)  # nº de fixtures futuros/sport


def planejar(
    kickoffs_por_sport: dict[str, list[datetime]],
    agora: datetime,
    cfg: CadenciaConfig = CadenciaConfig(),
) -> PlanoCiclo:
    """Decide (intervalo, ligas ativas) a partir do calendário e do relógio.

    Intervalo = pela proximidade GLOBAL (jogo mais próximo de qualquer liga).
    Ligas ativas = as que têm jogo em D+2. Se NENHUMA liga tem jogo à vista,
    devolve base e sports vazio (ciclo só dorme — zero crédito).
    """
    todos: list[datetime] = [k for ks in kickoffs_por_sport.values() for k in ks]
    ativos = tuple(s for s, ks in kickoffs_por_sport.items() if sport_ativo(ks, agora, cfg))
    return PlanoCiclo(
        intervalo_s=intervalo_s(todos, agora, cfg),
        sports=ativos,
        proximidade_min_s=_proximidade_min_s(todos, agora),
        kickoffs_por_sport={s: sum(1 for k in ks if k > agora) for s, ks in kickoffs_por_sport.items()},
    )


def ler_calendario(cliente: Any, sports: tuple[str, ...]) -> tuple[dict[str, list[datetime]], int]:
    """Lê os kickoffs de cada sport via `/events` (custo 0). Degradação segura:
    um sport que falhar entra com lista vazia (não derruba o planejamento). Devolve
    também o custo total de crédito observado (deve ser 0 — se não for, o rito vê).
    """
    kickoffs_por_sport: dict[str, list[datetime]] = {}
    custo_total = 0
    for sport in sports:
        try:
            resp = cliente.buscar_eventos(sport)
        except Exception as e:  # /events indisponível → sport sem calendário neste ciclo
            _log.warning("calendário do sport falhou (segue)", extra={"sport": sport, "erro": str(e)})
            kickoffs_por_sport[sport] = []
            continue
        custo_total += resp.custo_ultima or 0
        kickoffs_por_sport[sport] = [
            k for k in (parse_kickoff(f.get("commence_time")) for f in resp.fixtures) if k is not None
        ]
    return kickoffs_por_sport, custo_total
