"""CLI do L3 — notifica os sinais confirmados e entrega os alertas.

Roda na máquina do Daniel / VPS. Exige `.env` com Supabase + `TELEGRAM_BOT_TOKEN`
e `TELEGRAM_CHAT_ID` (as únicas credenciais desta camada — config por camada).

    python -m sinalizador.l3_notifica.cli --once
    python -m sinalizador.l3_notifica.cli --intervalo-s 30
    python -m sinalizador.l3_notifica.cli --descobrir-chat   # lista os chat_id que já falaram com o bot
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from urllib.request import urlopen

from sinalizador.comum.config import carregar_config
from sinalizador.comum.db import Banco
from sinalizador.comum.log import configurar_logging

from .bot import BotTelegram
from .notifica import processar

_log = logging.getLogger("l3.cli")


def _descobrir_chat(token: str) -> int:
    """Lista os chats que já mandaram mensagem ao bot (via getUpdates), com o
    `chat_id` de cada um — o valor que vai no `TELEGRAM_CHAT_ID`. Só precisa do
    token: não fala com o Supabase. Antes de rodar, abra o bot no Telegram e
    mande QUALQUER mensagem pra ele (ex.: /start) — senão a lista vem vazia."""
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    with urlopen(url, timeout=15.0) as resp:  # noqa: S310
        dados = json.loads(resp.read().decode("utf-8"))
    if not dados.get("ok"):
        print(f"[l3] getUpdates falhou: {dados}")
        return 1
    vistos: dict[int, str] = {}
    for upd in dados.get("result", []):
        msg = upd.get("message") or upd.get("channel_post") or {}
        chat = msg.get("chat") or {}
        cid = chat.get("id")
        if cid is None or cid in vistos:
            continue
        nome = chat.get("title") or " ".join(
            filter(None, [chat.get("first_name"), chat.get("last_name")])
        ) or chat.get("username") or "?"
        vistos[cid] = f"{nome} ({chat.get('type')})"
    if not vistos:
        print("[l3] nenhum chat encontrado. Abra o bot no Telegram e mande /start "
              "(ou qualquer mensagem) pra ele, depois rode este comando de novo.")
        return 1
    print("[l3] chats que já falaram com o bot — use o id no TELEGRAM_CHAT_ID:")
    for cid, desc in vistos.items():
        print(f"  chat_id={cid}  ->  {desc}")
    return 0


def main(argv: list[str] | None = None) -> int:
    configurar_logging()
    ap = argparse.ArgumentParser(description="L3 — notificação Telegram dos sinais e alertas")
    ap.add_argument("--once", action="store_true", help="processa um ciclo e sai")
    ap.add_argument("--intervalo-s", type=float, default=30.0, help="segundos entre ciclos")
    ap.add_argument("--limite", type=int, default=200, help="máx. de itens por ciclo")
    ap.add_argument("--descobrir-chat", action="store_true",
                    help="lista os chat_id que já falaram com o bot (só precisa do token) e sai")
    args = ap.parse_args(argv)

    cfg = carregar_config()
    if args.descobrir_chat:
        return _descobrir_chat(cfg.exigir("telegram_bot_token"))

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
