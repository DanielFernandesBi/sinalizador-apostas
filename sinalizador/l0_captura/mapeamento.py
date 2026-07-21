"""Tradução do formato The Odds API → domínio do sinalizador.

O parser só EXTRAI e NORMALIZA — nunca inventa. Outcome que não casa com nenhuma
seleção conhecida é descartado com aviso (P6: melhor não capturar do que capturar
errado). As seleções normalizadas são idênticas entre daemons (referência e
varejo compartilham este módulo), então os snapshots de mesma seleção casam no L1.

`ts_fonte` vem SEMPRE do carimbo da fonte (`last_update` do mercado, ou do
bookmaker se o mercado não trouxer) — nunca do relógio local (correção #1 / P6).
Sem `last_update` o snapshot é descartado: dado sem carimbo é dado ausente.
"""
from __future__ import annotations

import logging
from typing import Any, Iterator, Optional

_log = logging.getLogger(__name__)

# sport_key da The Odds API → rótulo de liga (as 6 europeias do backtest E6.1;
# alinhado a football_data.LIGAS). Ampliar aqui é decisão de escopo (D6).
SPORTS_ALVO: dict[str, str] = {
    "soccer_epl": "Premier League",
    "soccer_spain_la_liga": "La Liga",
    "soccer_italy_serie_a": "Serie A",
    "soccer_germany_bundesliga": "Bundesliga",
    "soccer_france_ligue_one": "Ligue 1",
    "soccer_portugal_primeira_liga": "Primeira Liga",
}

# market key da The Odds API → mercado do sistema (Doutrina P2: 1x2, AH, OU gols).
MERCADOS: dict[str, str] = {
    "h2h": "1x2",
    "spreads": "ah",
    "totals": "ou",
}

_EMPATE = {"draw", "tie", "empate", "x"}
_OVER = {"over", "o"}
_UNDER = {"under", "u"}

# --- Classificação de casa por chave de bookmaker (Sugestão nº 6, executável) ---
# O mesmo ciclo da região `eu` traz a Pinnacle + a exchange (Betfair) + ~20 casas
# de varejo NA MESMA resposta (custo de crédito zero adicional). Em vez de
# descartar tudo menos a Pinnacle, classifica-se cada casa e persiste-se todas.
CASA_REFERENCIA = "pinnacle"
# Listagens de troca (Betfair Exchange) na The Odds API: odds de exchange, porém
# SEM profundidade de book pela API (liquidez=None) — proxy DECLARADO de exchange
# (Sugestão nº 6). Comissão base 6,5% (Doutrina P4). Enquanto a API da Betfair
# (E1.2) não vier, é o preço de exchange que temos — capturado e rotulado.
EXCHANGES_PROXY = {"betfair_ex_eu", "betfair_ex_uk", "betfair_ex_au"}
COMISSAO_EXCHANGE_PCT = 6.5


def classificar_casa(chave: str) -> tuple[str, float]:
    """Chave de bookmaker (The Odds API) → (tipo, comissao_pct).

    Nunca descarta: o que não é referência nem exchange-proxy é varejo (venue do
    modo sombra). A comissão só é modelada para a exchange (6,5%); varejo de odd
    fixa não tem comissão de exchange (Doutrina §-sombra: slippage=0 é definição).
    """
    if chave == CASA_REFERENCIA:
        return ("referencia", 0.0)
    if chave in EXCHANGES_PROXY:
        return ("exchange", COMISSAO_EXCHANGE_PCT)
    return ("varejo", 0.0)


def liga_de(sport_key: str, sport_title: Optional[str] = None) -> str:
    return SPORTS_ALVO.get(sport_key) or sport_title or sport_key


def normalizar_evento(ev: dict[str, Any]) -> dict[str, Any]:
    """Evento cru da API → linha de `eventos` (ids_externos carrega o id da API)."""
    return {
        "esporte": "futebol",
        "liga": liga_de(ev.get("sport_key", ""), ev.get("sport_title")),
        "mandante": ev.get("home_team", ""),
        "visitante": ev.get("away_team", ""),
        "inicio_utc": ev.get("commence_time"),
        "ids_externos": {"odds_api": ev.get("id")},
    }


def _selecao(mercado: str, nome: str, home: str, away: str) -> Optional[str]:
    """Nome do outcome → código de seleção estável do sistema. None = desconhecido."""
    baixo = (nome or "").strip().lower()
    if mercado == "1x2":
        if nome == home:
            return "1"
        if nome == away:
            return "2"
        if baixo in _EMPATE:
            return "X"
        return None
    if mercado == "ou":
        if baixo in _OVER:
            return "over"
        if baixo in _UNDER:
            return "under"
        return None
    if mercado == "ah":
        if nome == home:
            return "mandante"
        if nome == away:
            return "visitante"
        return None
    return None


def iter_snapshots(
    ev: dict[str, Any], *, aceitar_casa=lambda _k: True
) -> Iterator[dict[str, Any]]:
    """Gera um dict de snapshot por (casa, mercado, outcome) do evento.

    Campos: casa, mercado, selecao, linha, odd, ts_fonte, raw. `aceitar_casa` filtra
    por chave de bookmaker (default: todas; a classificação por tipo é do ciclo).
    """
    home = ev.get("home_team", "")
    away = ev.get("away_team", "")
    for bk in ev.get("bookmakers", []):
        casa = bk.get("key")
        if not casa or not aceitar_casa(casa):
            continue
        ts_casa = bk.get("last_update")
        for mkt in bk.get("markets", []):
            mercado = MERCADOS.get(mkt.get("key", ""))
            if mercado is None:
                continue  # mercado fora do escopo homologável
            ts_fonte = mkt.get("last_update") or ts_casa
            if not ts_fonte:
                _log.warning(
                    "outcome sem carimbo de fonte descartado (P6)",
                    extra={"casa": casa, "mercado": mercado},
                )
                continue
            for out in mkt.get("outcomes", []):
                nome = out.get("name", "")
                selecao = _selecao(mercado, nome, home, away)
                odd = out.get("price")
                if selecao is None or odd is None:
                    _log.warning(
                        "outcome não reconhecido descartado",
                        extra={"casa": casa, "mercado": mercado, "nome": nome},
                    )
                    continue
                yield {
                    "casa": casa,
                    "mercado": mercado,
                    "selecao": selecao,
                    "linha": out.get("point"),  # None p/ 1x2; total/handicap p/ ou/ah
                    "odd": odd,
                    "ts_fonte": ts_fonte,
                    "raw": {"bookmaker": casa, "market": mkt.get("key"), "outcome": out},
                }
