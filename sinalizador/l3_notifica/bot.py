"""Cliente do bot Telegram (L3). Import do SDK não existe — é HTTP puro (urllib),
igual ao L0: roda na máquina do Daniel / VPS sem dependência externa.

O núcleo do L3 depende só do Protocol `Bot` (testável com fake). `BotTelegram` é a
implementação real (POST em `/sendMessage`), construída no `cli`.
"""
from __future__ import annotations

import json
import logging
from typing import Callable, Optional, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_log = logging.getLogger(__name__)

# transporte injetável: (url, corpo_json_bytes) -> (status, corpo). Padrão = urllib.
Transporte = Callable[[str, bytes], tuple[int, bytes]]


class Bot(Protocol):
    def enviar(self, texto: str) -> bool: ...


def _transporte_urllib(url: str, corpo: bytes, *, timeout: float = 15.0) -> tuple[int, bytes]:
    req = Request(url, data=corpo, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.status, resp.read()
    except HTTPError as e:
        return e.code, (e.read() if hasattr(e, "read") else b"")
    except URLError as e:
        raise ConnectionError(f"falha de rede ao falar com o Telegram: {e.reason}") from e


class BotTelegram:
    """Envia mensagens ao canal privado do Daniel via Bot API."""

    def __init__(
        self,
        token: str,
        chat_id: str,
        *,
        base_url: str = "https://api.telegram.org",
        transporte: Optional[Transporte] = None,
    ) -> None:
        self._token = token
        self._chat_id = chat_id
        self._base = base_url.rstrip("/")
        self._transporte = transporte or _transporte_urllib

    def enviar(self, texto: str) -> bool:
        """True se o Telegram aceitou (ok=true). Nunca levanta por HTTP: devolve
        False e loga — um alerta que falha vira retry no próximo ciclo, não crash."""
        url = f"{self._base}/bot{self._token}/sendMessage"
        corpo = json.dumps({"chat_id": self._chat_id, "text": texto,
                            "disable_web_page_preview": True}).encode("utf-8")
        try:
            status, resposta = self._transporte(url, corpo)
        except ConnectionError as e:
            _log.warning("envio Telegram falhou (rede) — retry no próximo ciclo", extra={"erro": str(e)})
            return False
        if status != 200:
            _log.warning("envio Telegram rejeitado", extra={"status": status,
                                                            "corpo": resposta[:200].decode("utf-8", "replace")})
            return False
        try:
            return bool(json.loads(resposta.decode("utf-8")).get("ok"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return False
