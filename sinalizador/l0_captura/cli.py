"""CLI do L0 — amarra o núcleo à infraestrutura real (Banco + The Odds API).

Roda na máquina do Daniel (E1 aceite #4) e depois no VPS (E0.5). Exige o `.env`
completo (Supabase + the_odds_api_key). Subcomandos:

    python -m sinalizador.l0_captura.cli cobertura           # E1 aceite #1 (fail-loud)
    python -m sinalizador.l0_captura.cli referencia --once   # E1.1 (um ciclo)
    python -m sinalizador.l0_captura.cli referencia --intervalo-s 3600
    python -m sinalizador.l0_captura.cli varejo --once       # E1.3
    python -m sinalizador.l0_captura.cli vigia --once        # E1.5

Cadência: no tier gratuito (500 créditos/mês) rode em intervalo alto. O custo de
crédito de cada ciclo é logado (x-requests-*) e sai no resumo — é ele que
dimensiona o tier pago (E1 aceite #2).
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

from sinalizador.comum.config import carregar_config
from sinalizador.comum.db import Banco
from sinalizador.comum.log import configurar_logging

from . import referencia, varejo, vigia
from .captura import MERCADOS_PADRAO, rodar_ciclo
from .cobertura import CoberturaInsuficienteError, inspecionar, verificar_ou_parar
from .mapeamento import SPORTS_ALVO
from .the_odds_api import ClienteOddsAPI

_log = logging.getLogger("l0_captura.cli")


def _cliente() -> ClienteOddsAPI:
    return ClienteOddsAPI(carregar_config().the_odds_api_key)


def _loop(rodar, *, intervalo_s: float) -> None:
    """Executa `rodar()` uma vez (intervalo<=0) ou em laço, tolerando falha de ciclo."""
    while True:
        try:
            rodar()
        except Exception:  # degradação segura: um ciclo ruim não derruba o daemon
            _log.exception("ciclo falhou — segue no próximo")
        if intervalo_s <= 0:
            return
        time.sleep(intervalo_s)


def _cmd_captura(perfil, args) -> int:
    banco, cliente = Banco(), _cliente()
    sports = tuple(args.sports) if args.sports else tuple(SPORTS_ALVO)
    markets = tuple(args.markets) if args.markets else MERCADOS_PADRAO

    def rodar() -> None:
        r = rodar_ciclo(banco, cliente, perfil, sports=sports, markets=markets)
        print(
            f"[{perfil.daemon}] snapshots={r.snapshots} eventos={r.eventos} "
            f"casas={r.casas_vistas} custo_creditos={r.custo_creditos} "
            f"restantes={r.creditos_restantes} sports_falha={r.sports_falha}"
        )

    _loop(rodar, intervalo_s=args.intervalo_s if not args.once else 0)
    return 0


def _cmd_cobertura(args) -> int:
    cob = inspecionar(_cliente(), sport=args.sport, market=args.market)
    print(cob.relatorio())
    try:
        verificar_ou_parar(cob)
    except CoberturaInsuficienteError as e:
        print(f"\nFALHA DE COBERTURA — PARAR: {e}", file=sys.stderr)
        return 2
    print("\nCobertura OK — pressuposto referência × line shopping sustentado.")
    return 0


def _cmd_vigia(args) -> int:
    banco = Banco()

    def rodar() -> None:
        mudos = vigia.rodar_vigia(banco, limiar_s=args.limiar_s,
                                  esperados=tuple(args.esperados) if args.esperados else vigia.DAEMONS_ESPERADOS_PADRAO)
        print(f"[vigia] mudos={[m['daemon'] for m in mudos] or 'nenhum'}")

    _loop(rodar, intervalo_s=args.intervalo_s if not args.once else 0)
    return 0


def main(argv: list[str] | None = None) -> int:
    configurar_logging()
    ap = argparse.ArgumentParser(description="L0 — captura de odds e vigia de heartbeats")
    sub = ap.add_subparsers(dest="cmd", required=True)

    for nome in ("referencia", "varejo"):
        p = sub.add_parser(nome, help=f"daemon {nome}")
        p.add_argument("--once", action="store_true", help="roda um ciclo e sai")
        p.add_argument("--intervalo-s", type=float, default=3600.0, help="segundos entre ciclos")
        p.add_argument("--sports", nargs="+", default=None, help="sport_keys (default: as 6 do backtest)")
        p.add_argument("--markets", nargs="+", default=None, help="h2h spreads totals")

    pc = sub.add_parser("cobertura", help="E1 aceite #1 — verifica cobertura (fail-loud)")
    pc.add_argument("--sport", default="soccer_epl")
    pc.add_argument("--market", default="h2h")

    pv = sub.add_parser("vigia", help="E1.5 — alerta daemon mudo")
    pv.add_argument("--once", action="store_true")
    pv.add_argument("--intervalo-s", type=float, default=1800.0)
    pv.add_argument("--limiar-s", type=float, default=vigia.LIMIAR_SILENCIO_S_PADRAO)
    pv.add_argument("--esperados", nargs="+", default=None)

    args = ap.parse_args(argv)
    if args.cmd == "referencia":
        return _cmd_captura(referencia.PERFIL, args)
    if args.cmd == "varejo":
        return _cmd_captura(varejo.PERFIL, args)
    if args.cmd == "cobertura":
        return _cmd_cobertura(args)
    if args.cmd == "vigia":
        return _cmd_vigia(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
