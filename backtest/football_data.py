"""Ingestão do histórico Football-Data.co.uk (E6.1).

Baixa e normaliza os CSVs de temporada das ligas-alvo. Mapeia as colunas para
os dois instantes que o dataset oferece (abertura e fechamento) — NÃO há
intradiário; essa limitação é declarada no cabeçalho do relatório (ver replay.py).

Mapeamento (por seleção):
  - referência (sharp) na ABERTURA  = Pinnacle, colunas PS* / P>2.5 etc.
  - referência (sharp) no FECHAMENTO = Pinnacle, colunas PSC* / PC>2.5 etc. (só medição)
  - venue                            = melhor preço entre as DEMAIS casas na abertura

Brasileirão NÃO é coberto pelo Football-Data → lacuna registrada como pendência
D6 no PLANO_MVP.
"""
from __future__ import annotations

import csv
import io
import urllib.request
from dataclasses import dataclass

BASE_URL = "https://www.football-data.co.uk/mmz4281"

# Ligas bem cobertas pelo Football-Data (divisão principal de cada país).
LIGAS: dict[str, str] = {
    "E0": "Inglaterra — Premier League",
    "SP1": "Espanha — La Liga",
    "I1": "Itália — Serie A",
    "D1": "Alemanha — Bundesliga",
    "F1": "França — Ligue 1",
    "P1": "Portugal — Primeira Liga",
}


@dataclass(frozen=True)
class SelecaoMercado:
    codigo: str
    col_ref_abertura: str    # Pinnacle abertura (de-vig por Shin junto do mercado)
    col_ref_fechamento: str  # Pinnacle fechamento (só medição de CLV)
    cols_venue: tuple[str, ...]  # casas de varejo (não-Pinnacle) na abertura


@dataclass(frozen=True)
class Mercado:
    nome: str
    selecoes: tuple[SelecaoMercado, ...]


# Mercados candidatos à homologação (Doutrina P2). AH fica para uma iteração
# seguinte (liquidação com linhas quarto e push exige tratamento próprio) —
# registrado como próximo passo do backtest.
MERCADOS: tuple[Mercado, ...] = (
    Mercado("1x2", (
        SelecaoMercado("H", "PSH", "PSCH", ("B365H", "BWH", "IWH", "WHH", "VCH", "LBH", "BFH")),
        SelecaoMercado("D", "PSD", "PSCD", ("B365D", "BWD", "IWD", "WHD", "VCD", "LBD", "BFD")),
        SelecaoMercado("A", "PSA", "PSCA", ("B365A", "BWA", "IWA", "WHA", "VCA", "LBA", "BFA")),
    )),
    # OU 2.5: no Football-Data a única casa de varejo consistente é o Bet365,
    # então o "melhor preço entre as demais casas" degenera para o B365 (limitação
    # de cobertura declarada no relatório).
    Mercado("ou_2.5", (
        SelecaoMercado("over", "P>2.5", "PC>2.5", ("B365>2.5",)),
        SelecaoMercado("under", "P<2.5", "PC<2.5", ("B365<2.5",)),
    )),
)


def url_csv(div: str, season: str) -> str:
    """URL do CSV de uma liga/temporada (season no formato '2324')."""
    return f"{BASE_URL}/{season}/{div}.csv"


def baixar_csv(div: str, season: str, *, timeout: float = 60.0) -> str:
    """Baixa o CSV (texto). Football-Data serve em latin-1."""
    with urllib.request.urlopen(url_csv(div, season), timeout=timeout) as resp:
        return resp.read().decode("latin-1")


def num(valor: object) -> float | None:
    """Converte célula em float; vazio/inválido → None (P6: nunca interpola)."""
    if valor is None:
        return None
    texto = str(valor).strip()
    if texto == "":
        return None
    try:
        return float(texto)
    except ValueError:
        return None


def carregar_partidas(csv_text: str, div: str | None = None) -> list[dict]:
    """Parseia o CSV em linhas (dict por partida). Ignora linhas sem HomeTeam.

    Anexa `_div` e `_liga` (nome legível). Não valida odds aqui — isso é do replay.
    """
    reader = csv.DictReader(io.StringIO(csv_text))
    partidas: list[dict] = []
    for row in reader:
        if not (row.get("HomeTeam") or "").strip():
            continue  # linha vazia / rodapé
        d = (row.get("Div") or (div or "")).strip()
        row["_div"] = d
        row["_liga"] = LIGAS.get(d, d or "?")
        partidas.append(row)
    return partidas
