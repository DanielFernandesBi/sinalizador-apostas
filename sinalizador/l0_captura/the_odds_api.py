"""Cliente da The Odds API (L0 — referência e varejo).

Sem dependência externa: usa `urllib` da stdlib para que o daemon rode na máquina
do Daniel (E1 aceite #4) e no VPS (E0.5) sem instalar nada além do projeto.

FALHA ALTO (Doutrina P6 — "dado ausente = abortar"): chave ausente, status HTTP
≠ 200, corpo vazio ou JSON inválido levantam exceção — nunca devolvem dado
"típico" nem silenciam o erro. Quem captura decide se aborta o ciclo.

Créditos (E1 aceite #2): a API devolve, em cada resposta, os headers
`x-requests-remaining`, `x-requests-used` e `x-requests-last` (custo da última
chamada). São lidos e anexados a cada `RespostaOdds` para que o ciclo logue o
consumo real — é esse número que dimensiona o tier pago (estamos no gratuito,
500/mês). O custo de uma chamada = nº de mercados × nº de regiões.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

_log = logging.getLogger(__name__)

BASE_URL_PADRAO = "https://api.the-odds-api.com/v4"

# Uma função de transporte: recebe uma URL, devolve (status, headers, corpo).
# Injetável para teste (sem rede); o padrão usa urllib.
Transporte = Callable[[str], "RespostaHTTP"]


class OddsAPIError(RuntimeError):
    """Falha ao obter dados da The Odds API — o ciclo deve abortar e registrar."""


@dataclass(frozen=True)
class RespostaHTTP:
    status: int
    headers: dict[str, str]
    corpo: bytes


@dataclass(frozen=True)
class RespostaOdds:
    """Eventos crus da API + metadados de crédito lidos dos headers."""

    eventos: list[dict[str, Any]]
    requests_remaining: Optional[int] = None
    requests_used: Optional[int] = None
    custo_ultima: Optional[int] = None  # x-requests-last (créditos da chamada)
    regioes: str = ""
    mercados: str = ""


def _int_ou_none(v: Optional[str]) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _transporte_urllib(url: str, *, timeout: float = 20.0) -> RespostaHTTP:
    req = Request(url, headers={"Accept": "application/json", "User-Agent": "sinalizador-l0/0.1"})
    try:
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310 (URL é construída aqui, não input)
            corpo = resp.read()
            headers = {k.lower(): v for k, v in resp.headers.items()}
            return RespostaHTTP(status=resp.status, headers=headers, corpo=corpo)
    except HTTPError as e:
        # 4xx/5xx: lê o corpo (a API explica o motivo, ex.: chave inválida, tier).
        corpo = e.read() if hasattr(e, "read") else b""
        headers = {k.lower(): v for k, v in (e.headers or {}).items()}
        return RespostaHTTP(status=e.code, headers=headers, corpo=corpo)
    except URLError as e:  # rede indisponível, DNS, TLS — falha de fonte (P6)
        raise OddsAPIError(f"falha de rede ao chamar The Odds API: {e.reason}") from e


class ClienteOddsAPI:
    """Chama /v4/sports/{sport}/odds e devolve eventos + metadados de crédito."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = BASE_URL_PADRAO,
        transporte: Optional[Transporte] = None,
    ) -> None:
        if not api_key:
            raise OddsAPIError("the_odds_api_key ausente — configure o .env (P6)")
        self._key = api_key
        self._base = base_url.rstrip("/")
        self._transporte = transporte or _transporte_urllib

    def buscar_odds(
        self,
        sport: str,
        *,
        regions: str,
        markets: str,
        odds_format: str = "decimal",
    ) -> RespostaOdds:
        """Odds de um sport em uma ou mais regiões/mercados. Falha alto em erro."""
        query = urlencode(
            {
                "apiKey": self._key,
                "regions": regions,
                "markets": markets,
                "oddsFormat": odds_format,
                "dateFormat": "iso",
            }
        )
        url = f"{self._base}/sports/{sport}/odds?{query}"
        resp = self._transporte(url)

        if resp.status != 200:
            trecho = resp.corpo[:300].decode("utf-8", "replace")
            raise OddsAPIError(
                f"The Odds API status {resp.status} para {sport} "
                f"(regions={regions}, markets={markets}): {trecho}"
            )
        try:
            eventos = json.loads(resp.corpo.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise OddsAPIError(f"corpo não-JSON da The Odds API para {sport}: {e}") from e
        if not isinstance(eventos, list):
            raise OddsAPIError(
                f"resposta inesperada da The Odds API para {sport}: esperava lista, "
                f"veio {type(eventos).__name__}"
            )

        h = resp.headers
        remaining = _int_ou_none(h.get("x-requests-remaining"))
        custo = _int_ou_none(h.get("x-requests-last"))
        _log.info(
            "the_odds_api chamada",
            extra={
                "sport": sport, "regions": regions, "markets": markets,
                "eventos": len(eventos), "creditos_restantes": remaining,
                "custo_creditos": custo,
            },
        )
        return RespostaOdds(
            eventos=eventos,
            requests_remaining=remaining,
            requests_used=_int_ou_none(h.get("x-requests-used")),
            custo_ultima=custo,
            regioes=regions,
            mercados=markets,
        )
