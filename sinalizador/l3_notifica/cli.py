"""CLI do L3 — notifica os sinais confirmados e entrega os alertas.

Roda na máquina do Daniel / VPS. Exige `.env` com Supabase + `TELEGRAM_BOT_TOKEN`
e `TELEGRAM_CHAT_ID` (as únicas credenciais desta camada — config por camada).

    python -m sinalizador.l3_notifica.cli --once
    python -m sinalizador.l3_notifica.cli --intervalo-s 30
"""
from __future__ import annotations

import argparse
import logging
import time

from sinalizador.comum.config import carregar_config
from sinalizador.comum.db import Banco
from sinalizador.comum.log import configurar_logging

from .bot import BotTelegram
from .notifica import processar

_log = logging.getLogger("l3.cli")


def main(argv: list[str] | None = None) -> int:
    configurar_logging()
    ap = argparse.ArgumentParser(description="L3 — notificação Telegram dos sinais e alertas")
    ap.add_argument("--once", action="store_true", help="processa um ciclo e sai")
    ap.add_argument("--intervalo-s", type=float, default=30.0, help="segundos entre ciclos")
    ap.add_argument("--limite", type=int, default=200, help="máx. de itens por ciclo")
    args = ap.parse_args(argv)

    cfg = carregar_config()
    banco = Banco()
    bot = BotTelegram(cfg.exigir("telegram_bot_token"), cfg.exigir("telegram_chat_id"))

    def rodar() -> None:
        r = processar(banco, bot, limite=args.limite)
        print(f"[l3] enviados={r.enviados} suprimidos={r.suprimidos} "
              f"expirados={r.expirados} alertas={r.alertas_entregues}")

    while True:
        try:
            rodar()
        except Exception:  # degradação segura: um ciclo ruim não derruba o daemon
            _log.exception("ciclo L3 falhou — segue no próximo")
        if args.once or args.intervalo_s <= 0:
            return 0
        time.sleep(args.intervalo_s)


if __name__ == "__main__":
    raise SystemExit(main())
