"""Sonda de avaliação da OddsPapi (PC-VENUE) — EXPERIMENTAL, não é integração.

Objetivo ÚNICO: avaliar se a OddsPapi serve como fonte das casas de varejo .bet.br
(que a The Odds API não cobre — não há região `br`). A sonda faz UMA chamada no
free tier e reporta:

  - casas brasileiras licenciadas presentes (heurística por marca — ver `MARCAS_BR`);
  - presença da Pinnacle (para cruzar referência × varejo na mesma fonte, se útil);
  - frescor (timestamps `last_update`/`updated_at` observados);
  - mercados cobertos.

NADA aqui grava no banco nem entra no fluxo — a decisão de adotar (ou não) a
OddsPapi é do RITO (PC-VENUE). O parsing é DEFENSIVO: os nomes de campo da OddsPapi
podem diferir; a sonda tenta as convenções comuns e, no que não reconhecer, reporta
"desconhecido" em vez de chutar. Ao ter a conta free, Daniel põe `ODDSPAPI_API_KEY`
no `.env`; a chave é cobrada só quando a sonda roda (config por camada).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from .the_odds_api import RespostaHTTP, Transporte, _transporte_urllib

_log = logging.getLogger(__name__)

# Base/rota são PROVISÓRIAS — confirmar contra a doc real da OddsPapi antes de usar.
BASE_URL_PADRAO = "https://api.oddspapi.io"

# Heurística de operadores .bet.br licenciados (marcas comuns; não exaustivo).
MARCAS_BR = {
    "bet365", "betano", "superbet", "kto", "betfair", "betnacional", "estrelabet",
    "f12", "betsson", "pixbet", "sportingbet", "novibet", "esportesdasorte",
    "vaidebet", "betfast", "brazino", "blaze", "betpix", "luvabet", "mcgames",
}


class SondaError(RuntimeError):
    """Falha ao consultar a OddsPapi na avaliação (não derruba nada — é sonda)."""


def _primeiro(d: dict, *chaves: str) -> Any:
    for k in chaves:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _casa_key(bk: dict) -> Optional[str]:
    v = _primeiro(bk, "key", "bookmaker", "name", "title", "id")
    return str(v).strip().lower() if v else None


def _e_brasileira(nome: str) -> bool:
    baixo = nome.lower()
    return ".bet.br" in baixo or any(m in baixo for m in MARCAS_BR)


@dataclass(frozen=True)
class RelatorioSonda:
    n_eventos: int
    casas: list[str] = field(default_factory=list)
    casas_brasileiras: list[str] = field(default_factory=list)
    pinnacle_presente: bool = False
    mercados: list[str] = field(default_factory=list)
    timestamps: list[str] = field(default_factory=list)

    def relatorio(self) -> str:
        return "\n".join([
            f"Sonda OddsPapi (avaliação PC-VENUE) — {self.n_eventos} evento(s):",
            f"  casas BR licenciadas (heurística): {self.casas_brasileiras or '(nenhuma reconhecida)'}",
            f"  Pinnacle presente: {'SIM' if self.pinnacle_presente else 'NÃO'}",
            f"  mercados cobertos: {self.mercados or '(desconhecido)'}",
            f"  frescor (últimos timestamps): {self.timestamps[:5] or '(sem carimbo)'}",
            f"  total de casas: {len(self.casas)}",
            "",
            "  EXPERIMENTAL — não integra nada; adoção é decisão do rito (PC-VENUE).",
        ])


def analisar(eventos: list[dict[str, Any]]) -> RelatorioSonda:
    """Extrai o relatório dos eventos crus (defensivo quanto ao schema). Puro/testável."""
    casas: set[str] = set()
    br: set[str] = set()
    mercados: set[str] = set()
    timestamps: list[str] = []
    for ev in eventos:
        books = _primeiro(ev, "bookmakers", "books", "sites", "odds") or []
        if isinstance(books, dict):
            books = list(books.values())
        for bk in books:
            if not isinstance(bk, dict):
                continue
            nome = _casa_key(bk)
            if not nome:
                continue
            casas.add(nome)
            if _e_brasileira(nome):
                br.add(nome)
            ts = _primeiro(bk, "last_update", "updated_at", "timestamp", "ts")
            if ts:
                timestamps.append(str(ts))
            mkts = _primeiro(bk, "markets", "bets") or []
            if isinstance(mkts, dict):
                mkts = list(mkts.values())
            for m in mkts:
                if isinstance(m, dict):
                    chave = _primeiro(m, "key", "name", "market", "id")
                    if chave:
                        mercados.add(str(chave))
    return RelatorioSonda(
        n_eventos=len(eventos),
        casas=sorted(casas),
        casas_brasileiras=sorted(br),
        pinnacle_presente="pinnacle" in casas,
        mercados=sorted(mercados),
        timestamps=sorted(timestamps, reverse=True),
    )


class SondaOddsPapi:
    """Cliente mínimo (urllib) para UMA chamada de avaliação. Não persiste nada."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = BASE_URL_PADRAO,
        transporte: Optional[Transporte] = None,
    ) -> None:
        if not api_key:
            raise SondaError("ODDSPAPI_API_KEY ausente — configure o .env para rodar a sonda")
        self._key = api_key
        self._base = base_url.rstrip("/")
        self._transporte = transporte or _transporte_urllib

    def buscar(self, caminho: str, params: Optional[dict[str, str]] = None) -> list[dict[str, Any]]:
        """GET `caminho` com a chave; devolve a lista de eventos. Falha alto em erro.

        `caminho`/`params` são passados pelo chamador porque a rota da OddsPapi ainda
        será confirmada na doc — a sonda não presume o endpoint.
        """
        from urllib.parse import urlencode
        q = dict(params or {})
        q.setdefault("apiKey", self._key)
        url = f"{self._base}/{caminho.lstrip('/')}?{urlencode(q)}"
        resp: RespostaHTTP = self._transporte(url)
        if resp.status != 200:
            trecho = resp.corpo[:300].decode("utf-8", "replace")
            raise SondaError(f"OddsPapi status {resp.status}: {trecho}")
        try:
            dados = json.loads(resp.corpo.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise SondaError(f"corpo não-JSON da OddsPapi: {e}") from e
        # Aceita lista direta ou {data:[...]} / {events:[...]} (defensivo).
        if isinstance(dados, dict):
            dados = _primeiro(dados, "data", "events", "results", "response") or []
        if not isinstance(dados, list):
            raise SondaError(f"resposta inesperada da OddsPapi: {type(dados).__name__}")
        return dados
