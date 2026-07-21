"""CLI do L4 — fechamento/CLV, registro de execução, liquidação e relatório.

    python -m sinalizador.l4_fechamento.cli fechar --once          # E5.1/E5.2
    python -m sinalizador.l4_fechamento.cli relatorio [--enviar]   # E5.5
    python -m sinalizador.l4_fechamento.cli apostei --sinal <id> --casa <id> --odd 2.05 --stake 20   # E5.3
    python -m sinalizador.l4_fechamento.cli liquidei --aposta <id> --resultado green --retorno 21    # E5.4
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime, timezone

from sinalizador.comum.config import carregar_config
from sinalizador.comum.db import Banco
from sinalizador.comum.log import configurar_logging

from .clv import rodar_fechamento
from .relatorio import formatar_relatorio

_log = logging.getLogger("l4.cli")


def _saldo(banco: Banco) -> float:
    banca = banco.banca_atual()
    try:
        return float(banca["saldo"]) if banca and banca.get("saldo") is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _cmd_fechar(banco: Banco, args) -> int:
    def rodar() -> None:
        r = rodar_fechamento(banco, datetime.now(timezone.utc).isoformat())
        print(f"[l4] eventos_fechados={r['eventos']} clv_gravadas={r['clv']}")

    while True:
        try:
            rodar()
        except Exception:
            _log.exception("ciclo L4 falhou — segue no próximo")
        if args.once or args.intervalo_s <= 0:
            return 0
        time.sleep(args.intervalo_s)


def _cmd_relatorio(banco: Banco, args) -> int:
    texto = formatar_relatorio(banco.clv_global(), banco.banca_atual(), banco.saude_daemons())
    print(texto)
    if args.enviar:
        from sinalizador.l3_notifica.bot import BotTelegram
        cfg = carregar_config()
        BotTelegram(cfg.exigir("telegram_bot_token"), cfg.exigir("telegram_chat_id")).enviar(texto)
    return 0


def _cmd_apostei(banco: Banco, args) -> int:
    ap = banco.registrar_aposta(
        sinal_id=args.sinal, casa_id=args.casa, odd_executada=args.odd,
        stake_valor=args.stake, saldo_antes=_saldo(banco),
    )
    print(f"[l4] aposta registrada id={ap.get('id')} stake={args.stake} @ {args.odd}")
    return 0


def _cmd_liquidei(banco: Banco, args) -> int:
    banco.liquidar_e_lancar(
        aposta_id=args.aposta, resultado=args.resultado,
        retorno_liquido=args.retorno, saldo_antes=_saldo(banco),
    )
    print(f"[l4] aposta {args.aposta} liquidada: {args.resultado} retorno={args.retorno}")
    return 0


def main(argv: list[str] | None = None) -> int:
    configurar_logging()
    ap = argparse.ArgumentParser(description="L4 — fechamento, CLV, execução e relatório")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pf = sub.add_parser("fechar", help="E5.1/E5.2 — fecha eventos iniciados e grava CLV")
    pf.add_argument("--once", action="store_true")
    pf.add_argument("--intervalo-s", type=float, default=900.0)

    pr = sub.add_parser("relatorio", help="E5.5 — relatório diário")
    pr.add_argument("--enviar", action="store_true", help="envia ao Telegram além de imprimir")

    pa = sub.add_parser("apostei", help="E5.3 — registra execução humana")
    pa.add_argument("--sinal", required=True)
    pa.add_argument("--casa", required=True)
    pa.add_argument("--odd", type=float, required=True)
    pa.add_argument("--stake", type=float, required=True)

    pl = sub.add_parser("liquidei", help="E5.4 — liquida uma aposta")
    pl.add_argument("--aposta", required=True)
    pl.add_argument("--resultado", required=True,
                    choices=["green", "red", "void", "meio_green", "meio_red"])
    pl.add_argument("--retorno", type=float, required=True, help="retorno líquido (com sinal)")

    args = ap.parse_args(argv)
    banco = Banco()
    if args.cmd == "fechar":
        return _cmd_fechar(banco, args)
    if args.cmd == "relatorio":
        return _cmd_relatorio(banco, args)
    if args.cmd == "apostei":
        return _cmd_apostei(banco, args)
    if args.cmd == "liquidei":
        return _cmd_liquidei(banco, args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
