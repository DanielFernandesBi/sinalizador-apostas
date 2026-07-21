"""CLI do L2 — roda o crivo (IA) sobre a fila de sinais `aguardando_crivo`.

Roda na máquina do Daniel / VPS. Exige `.env` com Supabase e `ANTHROPIC_API_KEY`
(a única credencial desta camada — config por camada). O system prompt é o Manual
do Crivo VIGENTE, lido da `config_sistema` (nunca hard-coded).

    python -m sinalizador.l2_crivo.cli --once            # processa a fila uma vez e sai
    python -m sinalizador.l2_crivo.cli --intervalo-s 30  # laço contínuo

Invariante: nenhuma falha aprova um sinal. Exceção no caminho → sinal `erro`.
"""
from __future__ import annotations

import argparse
import logging
import time

from sinalizador.comum.config import carregar_config
from sinalizador.comum.db import Banco
from sinalizador.comum.log import configurar_logging

from .crivo import processar_fila
from .modelo import ModeloAnthropic

_log = logging.getLogger("l2.cli")


def main(argv: list[str] | None = None) -> int:
    configurar_logging()
    ap = argparse.ArgumentParser(description="L2 — crivo (IA) sobre sinais aguardando_crivo")
    ap.add_argument("--once", action="store_true", help="processa a fila uma vez e sai")
    ap.add_argument("--intervalo-s", type=float, default=30.0, help="segundos entre ciclos")
    ap.add_argument("--limite", type=int, default=50, help="máx. de sinais por ciclo")
    args = ap.parse_args(argv)

    cfg = carregar_config()
    banco = Banco()
    modelo = ModeloAnthropic(cfg.exigir("anthropic_api_key"))

    def rodar() -> None:
        r = processar_fila(banco, modelo, limite=args.limite)
        print(f"[l2] avaliados={r.avaliados} confirmados={r.confirmados} "
              f"vetados={r.vetados} erros={r.erros}")

    while True:
        try:
            rodar()
        except Exception:  # degradação segura: um ciclo ruim não derruba o daemon
            _log.exception("ciclo L2 falhou — segue no próximo")
        if args.once or args.intervalo_s <= 0:
            return 0
        time.sleep(args.intervalo_s)


if __name__ == "__main__":
    raise SystemExit(main())
