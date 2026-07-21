"""Ciclo de captura do L0 — motor compartilhado pelos daemons de captura.

Um ciclo: para cada sport alvo, chama a The Odds API na região do perfil, faz
get-or-create de eventos/casas e monta um snapshot por outcome (`ts_fonte` da
fonte). Ao fim, grava TODOS os snapshots do ciclo em UM POST (higiene de saída)
e PULSA o heartbeat do daemon (E1.5) com o resumo — inclusive o consumo de
créditos do ciclo (E1 aceite #2). Um sport que falha na API não derruba o ciclo:
registra a falha e segue (degradação segura), e o heartbeat sai com a contagem de
falhas para o vigia enxergar.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

from .mapeamento import SPORTS_ALVO, iter_snapshots, normalizar_evento
from .persistencia import BancoL0, garantir_casa, garantir_evento, linha_snapshot
from .the_odds_api import ClienteOddsAPI, OddsAPIError

_log = logging.getLogger(__name__)

# Mercados capturados por padrão (Doutrina P2). O custo de crédito de uma chamada
# = nº de mercados × nº de regiões; por isso a lista é explícita e configurável.
MERCADOS_PADRAO = ("h2h", "spreads", "totals")


@dataclass(frozen=True)
class PerfilCaptura:
    """O que distingue um daemon de captura (região + como classificar as casas).

    `classificar(chave)` devolve `(tipo, comissao_pct)` para a casa, ou `None`
    para descartá-la. O perfil da região `eu` classifica TODAS as casas (Sugestão
    nº 6): referência (Pinnacle), exchange-proxy (Betfair) e varejo — nada se
    descarta, já que os snapshots vêm na mesma resposta (crédito zero adicional).
    """

    daemon: str                                   # nome em `heartbeats`/`vw_saude_daemons`
    regiao: str                                   # 'eu' (referência+venues) | 'br' (varejo)
    classificar: Callable[[str], Optional[tuple[str, float]]]


@dataclass
class ResumoCiclo:
    snapshots: int = 0
    eventos: int = 0
    casas_vistas: int = 0             # casas distintas com snapshot no ciclo
    por_tipo: dict[str, int] = field(default_factory=dict)  # casas distintas por tipo
    creditos_restantes: int | None = None
    creditos_usados: int | None = None
    custo_creditos: int = 0           # soma do custo por chamada (x-requests-last)
    sports_ok: int = 0
    sports_falha: list[str] = field(default_factory=list)


def rodar_ciclo(
    banco: BancoL0,
    cliente: ClienteOddsAPI,
    perfil: PerfilCaptura,
    *,
    sports=tuple(SPORTS_ALVO),
    markets=MERCADOS_PADRAO,
) -> ResumoCiclo:
    """Roda um ciclo completo e pulsa o heartbeat. Devolve o resumo para o log/CLI."""
    markets_str = ",".join(markets)
    resumo = ResumoCiclo()
    cache_casas: dict[str, str] = {}
    tipos_por_casa: dict[str, str] = {}       # nome → tipo (para o resumo por tipo)
    linhas: list[dict] = []                    # snapshots do ciclo → 1 INSERT em lote

    for sport in sports:
        try:
            resp = cliente.buscar_odds(sport, regions=perfil.regiao, markets=markets_str)
        except OddsAPIError as e:
            _log.warning("captura de sport falhou (segue o ciclo)", extra={"sport": sport, "erro": str(e)})
            resumo.sports_falha.append(sport)
            continue

        resumo.sports_ok += 1
        if resp.custo_ultima is not None:
            resumo.custo_creditos += resp.custo_ultima
        if resp.requests_remaining is not None:
            resumo.creditos_restantes = resp.requests_remaining
        if resp.requests_used is not None:
            resumo.creditos_usados = resp.requests_used

        for ev in resp.eventos:
            evento_id = garantir_evento(banco, normalizar_evento(ev))
            if evento_id is None:
                continue
            resumo.eventos += 1
            for snap in iter_snapshots(ev):
                classe = perfil.classificar(snap["casa"])
                if classe is None:
                    continue  # casa fora do perfil (descarte explícito)
                tipo, comissao = classe
                casa_id = garantir_casa(banco, snap["casa"], tipo=tipo,
                                        comissao_pct=comissao, cache=cache_casas)
                if casa_id is None:
                    continue
                tipos_por_casa[snap["casa"]] = tipo
                linhas.append(linha_snapshot(evento_id=evento_id, casa_id=casa_id, snap=snap))

    # Grava todos os snapshots do ciclo em UM POST (higiene de saída: N linhas em
    # vez de N POSTs — corta o ciclo de dezenas de segundos para poucos).
    if linhas:
        banco.inserir_muitos("odds_snapshots", linhas)
    resumo.snapshots = len(linhas)
    resumo.casas_vistas = len(cache_casas)
    for tipo in tipos_por_casa.values():
        resumo.por_tipo[tipo] = resumo.por_tipo.get(tipo, 0) + 1

    detalhe = {
        "snapshots": resumo.snapshots,
        "eventos": resumo.eventos,
        "regiao": perfil.regiao,
        "casas_por_tipo": resumo.por_tipo,
        "creditos_restantes": resumo.creditos_restantes,
        "custo_creditos": resumo.custo_creditos,
        "sports_ok": resumo.sports_ok,
        "sports_falha": resumo.sports_falha,
    }
    banco.pulsar(perfil.daemon, detalhe)
    _log.info("ciclo de captura concluído", extra={"daemon": perfil.daemon, **detalhe})
    return resumo
