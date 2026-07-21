"""CLI do L1 — roda o pipeline mecânico sobre os snapshots reais do L0.

Roda na máquina do Daniel / VPS. Exige `.env` (Supabase) e uma banca semeada em
`banca_ledger` (mesmo em modo sombra: um aporte nocional é a banca de papel).

    python -m sinalizador.l1_gatilhos.cli --once                     # venue retail_sombra (modo sombra)
    python -m sinalizador.l1_gatilhos.cli --once --venue exchange    # doutrina-puro (só com exchange capturável)
    python -m sinalizador.l1_gatilhos.cli --intervalo-s 60

Padrão `retail_sombra`: RATIFICADO pela Sugestão nº 6 como o venue do modo sombra
(sem API da Betfair não há exchange capturável; o modo sombra mede CLV, que não
exige book). O sinal nasce `sombra_varejo=true` e a proteção é a `odd_minima_aceitavel`
contra o preço real no app. Dinheiro real segue travado pelo gate do E7. Use
`--venue exchange` quando houver venue de exchange capturável (E1.2/exchange-proxy).
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime, timezone

from sinalizador.comum.db import Banco
from sinalizador.comum.gates import CarregadorGates
from sinalizador.comum.log import configurar_logging

from .orquestrador import PoliticaVenue, rodar_l1

_log = logging.getLogger("l1.cli")


def main(argv: list[str] | None = None) -> int:
    configurar_logging()
    ap = argparse.ArgumentParser(description="L1 — gatilhos sobre snapshots reais do L0")
    ap.add_argument("--once", action="store_true", help="roda um ciclo e sai")
    ap.add_argument("--intervalo-s", type=float, default=60.0, help="segundos entre ciclos")
    ap.add_argument("--lookback-s", type=float, default=3600.0, help="janela de snapshots (s)")
    ap.add_argument("--venue", choices=[p.value for p in PoliticaVenue],
                    default=PoliticaVenue.RETAIL_SOMBRA.value)  # modo sombra (Sugestão nº 6)
    args = ap.parse_args(argv)

    banco = Banco()
    gates = CarregadorGates(banco)
    gates.validar_integridade()  # falha alto se a integridade dos gates estiver violada
    politica = PoliticaVenue(args.venue)

    def rodar() -> None:
        r = rodar_l1(banco, gates, agora=datetime.now(timezone.utc),
                     politica=politica, lookback_s=args.lookback_s)
        print(f"[l1] grupos={r.grupos} sinais={r.sinais} abortos={r.abortos} "
              f"rastreados_clv={r.rastreados_clv} venue={politica.value}")

    while True:
        try:
            rodar()
        except Exception:  # degradação segura: um ciclo ruim não derruba o daemon
            _log.exception("ciclo L1 falhou — segue no próximo")
        if args.once or args.intervalo_s <= 0:
            return 0
        time.sleep(args.intervalo_s)


if __name__ == "__main__":
    raise SystemExit(main())
