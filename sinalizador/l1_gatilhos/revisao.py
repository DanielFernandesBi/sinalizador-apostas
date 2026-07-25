"""Revisão indivisível do book — conceito ÚNICO, compartilhado por L1 e L4 (P0.3).

Uma revisão é o estado do mercado numa casa num instante:

    (casa_id, mercado, linha canônica, ts_fonte) → {seleção: odd}

Ela é INDIVISÍVEL. Um book só serve se contiver TODAS as seleções canônicas do
mercado naquele mesmo carimbo; revisão incompleta é descartada inteira, nunca
completada com preços de outra. Tomar o último preço de cada seleção
independentemente monta um book que **nunca existiu** — preço fresco de duas
seleções casado com preço velho da terceira, o que acontece sempre que a revisão
mais recente omite uma seleção (suspensão perto do início, payload parcial).

POR QUE ESTE MÓDULO EXISTE: o defeito foi corrigido no L4 (fechamento) e
sobreviveu no L1 (emissão), porque cada camada tinha a sua própria montagem de
book. No L1 é pior: lá o book determina a `p_justa`, logo o edge, logo a
EXISTÊNCIA do sinal — no L4 contamina só a medição. Uma definição só, usada pelos
dois, é o que impede a divergência de voltar.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from sinalizador.comum.tempo import para_datetime

from .devig import devig_shin

# Ordem canônica das seleções por mercado. Book de referência sem TODAS elas → sem
# de-vig (P6). Mora aqui porque é o que define "revisão completa" para as duas camadas.
ORDEM_SELECAO: dict[str, tuple[str, ...]] = {
    "1x2": ("1", "X", "2"),
    "ou": ("over", "under"),
    "ah": ("mandante", "visitante"),
}


def linha_key(linha: Any) -> Optional[float]:
    """Normaliza a linha (2 casas). None permanece None — é a chave do 1x2."""
    if linha is None:
        return None
    try:
        return round(float(linha), 2)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class Revisao:
    """Um book completo de uma casa num instante."""
    casa_id: str
    mercado: str
    linha: Optional[float]
    ts_fonte: datetime
    odds: dict[str, float]

    def probs(self) -> dict[str, float]:
        """Probabilidades justas (Shin) desta revisão — só das odds DELA."""
        ordem = ORDEM_SELECAO[self.mercado]
        p, _z = devig_shin([self.odds[sel] for sel in ordem])
        return dict(zip(ordem, p))


def agrupar_por_revisao(snaps: list[dict[str, Any]]) -> dict[tuple, dict[str, float]]:
    """Agrupa snapshots pela unidade indivisível `(casa_id, mercado, linha, ts_fonte)`.

    `casa_id` entra na chave de propósito: havendo mais de uma casa de referência,
    o book jamais pode ser montado juntando seleções de referências distintas.
    """
    revisoes: dict[tuple, dict[str, float]] = {}
    for s in snaps:
        if s.get("odd") is None:
            continue
        ts = para_datetime(s.get("ts_fonte"))
        if ts is None:
            continue  # sem carimbo não há revisão a que pertencer (P6)
        chave = (s.get("casa_id"), s["mercado"], linha_key(s.get("linha")), ts)
        revisoes.setdefault(chave, {})[s["selecao"]] = float(s["odd"])
    return revisoes


def revisoes_completas(snaps: list[dict[str, Any]], *,
                       ate: Optional[datetime] = None) -> list[Revisao]:
    """Todas as revisões COMPLETAS presentes nos snapshots, mais nova primeiro.

    `ate` descarta revisões posteriores a um instante (o L4 usa o início da partida;
    o L1, o `agora` do ciclo). Mercado fora de `ORDEM_SELECAO` é ignorado — está
    fora de escopo, não é dado faltando.
    """
    out: list[Revisao] = []
    for (casa_id, mercado, linha, ts), odds in agrupar_por_revisao(snaps).items():
        ordem = ORDEM_SELECAO.get(mercado)
        if ordem is None:
            continue
        if ate is not None and ts > ate:
            continue
        if any(sel not in odds for sel in ordem):
            continue  # incompleta: descartada INTEIRA
        out.append(Revisao(casa_id=casa_id, mercado=mercado, linha=linha,
                           ts_fonte=ts, odds=odds))
    # mais recente primeiro; empate entre casas resolvido por casa_id (determinismo)
    out.sort(key=lambda r: (r.ts_fonte, r.casa_id), reverse=True)
    return out


def ultima_revisao_completa(snaps: list[dict[str, Any]], *, mercado: str,
                            linha: Optional[float],
                            ate: Optional[datetime] = None) -> Optional[Revisao]:
    """A revisão completa mais recente de um (mercado, linha). None se não houver."""
    alvo = linha_key(linha)
    for rev in revisoes_completas(snaps, ate=ate):
        if rev.mercado == mercado and rev.linha == alvo:
            return rev
    return None


def contar_revisoes(snaps: list[dict[str, Any]], *, casa_id: str, mercado: str,
                    linha: Optional[float], selecao: str,
                    desde: Optional[datetime] = None,
                    ate: Optional[datetime] = None) -> int:
    """Quantas revisões DISTINTAS (ts_fonte distintos) existem para uma seleção.

    É a unidade certa para medir movimento (P0.4): o mesmo estado de mercado
    recapturado N vezes é UMA revisão, não N pontos. Sem isso, recaptura vira
    "movimento medido" e ausência de histórico vira "referência estável".
    """
    alvo = linha_key(linha)
    carimbos: set[datetime] = set()
    for s in snaps:
        if (s.get("casa_id") != casa_id or s.get("mercado") != mercado
                or linha_key(s.get("linha")) != alvo or s.get("selecao") != selecao
                or s.get("odd") is None):
            continue
        ts = para_datetime(s.get("ts_fonte"))
        if ts is None or (desde is not None and ts < desde) or (ate is not None and ts > ate):
            continue
        carimbos.add(ts)
    return len(carimbos)
