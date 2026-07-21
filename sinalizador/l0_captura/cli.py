"""CLI do L0 — amarra o núcleo à infraestrutura real (Banco + The Odds API).

Roda na máquina do Daniel (E1 aceite #4) e depois no VPS (E0.5). Config por camada:
cada subcomando exige só o segredo que usa (P6 no ponto de uso). Subcomandos:

    python -m sinalizador.l0_captura.cli cobertura           # E1 aceite #1 (fail-loud na Pinnacle)
    python -m sinalizador.l0_captura.cli referencia --once   # E1.1 (um ciclo)
    python -m sinalizador.l0_captura.cli referencia --intervalo-s 3600
    python -m sinalizador.l0_captura.cli referencia --adaptativo   # cadência por proximidade do jogo
    python -m sinalizador.l0_captura.cli varejo --once       # E1.3
    python -m sinalizador.l0_captura.cli sonda --caminho ... # avaliação PC-VENUE (OddsPapi)
    python -m sinalizador.l0_captura.cli vigia --once        # E1.5

Cadência: no tier gratuito (500 créditos/mês) use `--adaptativo` (gasta crédito só
perto dos jogos). O custo de cada ciclo é logado (x-requests-*) e sai no resumo —
é ele que dimensiona o tier pago (E1 aceite #2).
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone

from sinalizador.comum.config import carregar_config
from sinalizador.comum.db import Banco
from sinalizador.comum.log import configurar_logging

from . import cadencia, referencia, varejo, vigia
from .captura import MERCADOS_PADRAO, rodar_ciclo
from .cobertura import CoberturaInsuficienteError, inspecionar, verificar_ou_parar
from .mapeamento import SPORTS_ALVO
from .sonda_oddspapi import SondaOddsPapi, analisar
from .the_odds_api import ClienteOddsAPI

_log = logging.getLogger("l0_captura.cli")


def _cliente() -> ClienteOddsAPI:
    # Config por camada: cobra só a chave que ESTE processo usa (P6 no ponto de uso).
    return ClienteOddsAPI(carregar_config().exigir("the_odds_api_key"))


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

    if args.adaptativo:
        return _loop_adaptativo(banco, cliente, perfil, sports, markets, args)

    def rodar() -> None:
        r = rodar_ciclo(banco, cliente, perfil, sports=sports, markets=markets)
        print(
            f"[{perfil.daemon}] snapshots={r.snapshots} eventos={r.eventos} "
            f"casas={r.casas_vistas} custo_creditos={r.custo_creditos} "
            f"restantes={r.creditos_restantes} sports_falha={r.sports_falha}"
        )

    _loop(rodar, intervalo_s=args.intervalo_s if not args.once else 0)
    return 0


def _loop_adaptativo(banco, cliente, perfil, sports, markets, args) -> int:
    """Loop com cadência adaptativa (proximidade do jogo). O calendário (`/events`,
    custo 0) é lido a cada `--calendario-intervalo-s` e cacheado; a cada ciclo a
    política decide o intervalo e QUAIS ligas consultar (só as com jogo em D+2)."""
    cfg = cadencia.CadenciaConfig(base_s=args.base_s)
    cache: dict[str, list] = {}
    ultimo_calendario: datetime | None = None
    while True:
        intervalo = cfg.base_s
        try:
            agora = datetime.now(timezone.utc)
            venceu = (ultimo_calendario is None
                      or (agora - ultimo_calendario).total_seconds() >= args.calendario_intervalo_s)
            if venceu:
                cache, custo_cal = cadencia.ler_calendario(cliente, sports)
                ultimo_calendario = agora
                _log.info("calendário atualizado", extra={"custo_creditos": custo_cal,
                                                          "sports": len(cache)})
            plano = cadencia.planejar(cache, agora, cfg)
            intervalo = plano.intervalo_s
            r = rodar_ciclo(banco, cliente, perfil, sports=plano.sports, markets=markets)
            prox_min = int(plano.proximidade_min_s) if plano.proximidade_min_s is not None else None
            print(
                f"[{perfil.daemon}] adaptativo intervalo={intervalo:.0f}s "
                f"ligas_ativas={list(plano.sports) or 'nenhuma'} prox_jogo_s={prox_min} "
                f"snapshots={r.snapshots} custo_creditos={r.custo_creditos} "
                f"restantes={r.creditos_restantes}"
            )
        except Exception:  # degradação segura: um ciclo ruim não derruba o daemon
            _log.exception("ciclo adaptativo falhou — segue no próximo")
        if args.once:
            return 0
        time.sleep(intervalo)


def _cmd_cobertura(args) -> int:
    cob = inspecionar(_cliente(), sport=args.sport, market=args.market)
    print(cob.relatorio())
    try:
        verificar_ou_parar(cob)
    except CoberturaInsuficienteError as e:
        print(f"\nFALHA DE COBERTURA — PARAR: {e}", file=sys.stderr)
        return 2
    print("\nCobertura OK — Pinnacle presente na eu (pressuposto da referência sustentado).")
    return 0


def _cmd_sonda(args) -> int:
    # Avaliação PC-VENUE: não persiste nada, não integra — só reporta.
    sonda = SondaOddsPapi(carregar_config().exigir("oddspapi_api_key"))
    params = dict(p.split("=", 1) for p in (args.param or []))
    eventos = sonda.buscar(args.caminho, params)
    print(analisar(eventos).relatorio())
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
        p.add_argument("--intervalo-s", type=float, default=3600.0, help="segundos entre ciclos (modo fixo)")
        p.add_argument("--sports", nargs="+", default=None, help="sport_keys (default: as 6 do backtest)")
        p.add_argument("--markets", nargs="+", default=None, help="h2h spreads totals")
        p.add_argument("--adaptativo", action="store_true",
                       help="cadência por proximidade do jogo (5/10/60 min) + só ligas com jogo em D+2")
        p.add_argument("--base-s", type=float, default=3600.0,
                       help="intervalo base do modo adaptativo (sem jogo próximo)")
        p.add_argument("--calendario-intervalo-s", type=float, default=3600.0,
                       help="de quanto em quanto tempo o calendário /events (custo 0) é relido")

    pc = sub.add_parser("cobertura", help="E1 aceite #1 — cobertura eu (fail-loud só na Pinnacle)")
    pc.add_argument("--sport", default="soccer_epl")
    pc.add_argument("--market", default="h2h")

    ps = sub.add_parser("sonda", help="avaliação PC-VENUE — sonda OddsPapi (experimental)")
    ps.add_argument("--caminho", required=True, help="rota da OddsPapi (ver doc); ex.: v1/odds")
    ps.add_argument("--param", nargs="*", default=None, help="k=v repetível (ex.: sport=soccer)")

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
    if args.cmd == "sonda":
        return _cmd_sonda(args)
    if args.cmd == "vigia":
        return _cmd_vigia(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
