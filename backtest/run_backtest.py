"""CLI do backtest (E6.1 + E6.2): baixa (ou lê local), roda o replay, escreve saídas.

Uso (no VPS/dev com rede aberta):
    python -m backtest.run_backtest --ligas E0 SP1 I1 D1 F1 P1 --temporadas 2324 2223 --saida ./saida_backtest

Modo offline (CSVs já baixados em <dir>/<DIV>_<SEASON>.csv), útil sem rede:
    python -m backtest.run_backtest --offline ./csvs --ligas E0 --temporadas 2324 --saida ./saida
"""
from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone

from .football_data import LIGAS, baixar_csv, carregar_partidas
from .replay import (
    AMOSTRA_MINIMA,
    EDGE_MIN_PROV,
    ODD_TETO_PROV,
    agregar_celulas,
    escrever_saidas,
    replay,
)


def _carregar(offline: str | None, div: str, season: str) -> list[dict]:
    if offline:
        caminho = os.path.join(offline, f"{div}_{season}.csv")
        with open(caminho, encoding="latin-1") as f:
            texto = f.read()
    else:
        texto = baixar_csv(div, season)
    return carregar_partidas(texto, div=div)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Backtest do L1 (value_bet) sobre Football-Data")
    ap.add_argument("--ligas", nargs="+", default=list(LIGAS), choices=list(LIGAS))
    ap.add_argument("--temporadas", nargs="+", required=True, help="ex.: 2324 2223")
    ap.add_argument("--saida", default="./saida_backtest")
    ap.add_argument("--offline", default=None, help="dir com CSVs <DIV>_<SEASON>.csv")
    ap.add_argument("--edge-min", type=float, default=EDGE_MIN_PROV)
    ap.add_argument("--odd-teto", type=float, default=ODD_TETO_PROV)
    args = ap.parse_args(argv)

    partidas: list[dict] = []
    faltas: list[str] = []
    for div in args.ligas:
        for season in args.temporadas:
            try:
                partidas.extend(_carregar(args.offline, div, season))
            except Exception as e:  # rede/arquivo — registra e segue (degradação segura)
                faltas.append(f"{div}/{season}: {e}")

    candidatos = replay(partidas, edge_min=args.edge_min, odd_teto=args.odd_teto)
    celulas = agregar_celulas(candidatos)
    meta = {
        "gerado_em": datetime.now(timezone.utc).isoformat(),
        "ligas": [LIGAS[d] for d in args.ligas],
        "temporadas": args.temporadas,
        "edge_min": args.edge_min,
        "odd_teto": args.odd_teto,
        "amostra_minima": AMOSTRA_MINIMA,
        "partidas": len(partidas),
        "fontes_faltantes": faltas,
    }
    escrever_saidas(candidatos, celulas, args.saida, meta=meta)
    print(f"partidas={len(partidas)} candidatos={len(candidatos)} celulas={len(celulas)} -> {args.saida}")
    if faltas:
        print("fontes faltantes:", *faltas, sep="\n  ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
