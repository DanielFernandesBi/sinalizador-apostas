"""Logging estruturado (JSON) com propagação de contexto (sinal_id/evento_id).

Um único formato JSON para todos os daemons, com contexto amarrado ao logger
(via `get_logger(nome, sinal_id=..., evento_id=...)`) e/ou por chamada (`extra=`).
Os dois se fundem — o do logger é a base, o da chamada tem prioridade.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

# Atributos padrão de LogRecord — tudo que NÃO estiver aqui é tratado como
# contexto extra e vai para o JSON.
_RESERVADOS = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "taskName", "message", "asctime",
}


class FormatadorJSON(logging.Formatter):
    """Serializa cada registro como uma linha JSON."""

    def format(self, record: logging.LogRecord) -> str:
        saida: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "nivel": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for chave, valor in record.__dict__.items():
            if chave not in _RESERVADOS and not chave.startswith("_"):
                saida[chave] = valor
        if record.exc_info:
            saida["exc"] = self.formatException(record.exc_info)
        return json.dumps(saida, ensure_ascii=False, default=str)


class _AdaptadorContexto(logging.LoggerAdapter):
    """Funde o contexto amarrado ao logger com o `extra` de cada chamada."""

    def process(self, msg: Any, kwargs: Any) -> tuple[Any, Any]:
        extra = {**(self.extra or {}), **kwargs.get("extra", {})}
        kwargs["extra"] = extra
        return msg, kwargs


# Bibliotecas ruidosas: o cliente HTTP do supabase-py (httpx/httpcore) loga cada
# requisição em INFO — num ciclo de captura isso vira "cachoeira" de INSERTs no
# terminal. Rebaixados a WARNING (higiene de saída); o que interessa (heartbeat,
# resumo do ciclo) continua saindo pelos nossos loggers.
_BIBLIOTECAS_SILENCIADAS = ("httpx", "httpcore", "hpack", "urllib3")


def configurar_logging(nivel: str = "INFO") -> None:
    """Instala o formatador JSON no logger raiz. Idempotente."""
    raiz = logging.getLogger()
    raiz.setLevel(nivel)
    for handler in list(raiz.handlers):
        raiz.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(FormatadorJSON())
    raiz.addHandler(handler)
    for nome in _BIBLIOTECAS_SILENCIADAS:
        logging.getLogger(nome).setLevel(logging.WARNING)


def get_logger(nome: str, **contexto: Any) -> logging.LoggerAdapter:
    """Logger com contexto (ex.: sinal_id/evento_id) propagado em todo registro."""
    return _AdaptadorContexto(logging.getLogger(nome), contexto)
