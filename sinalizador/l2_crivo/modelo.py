"""E3.1/E3.2 — cliente do modelo (Anthropic) para o L2 (crivo).

O núcleo do crivo (`crivo.py`) depende só do Protocol `ModeloCrivo` — testável com
fake, sem SDK nem rede. `ModeloAnthropic` é a implementação real, construída no CLI.

Regras da camada (Doutrina / Manual §8):
  - O **system prompt é o Manual do Crivo vigente** lido da `config_sistema` (nunca
    hard-coded) — quem passa o texto é o chamador (`crivo.py`).
  - Modelo forte. **Sem `temperature`** — o modelo a rejeita (400); o determinismo
    é buscado por `effort` baixo + instrução, não por temp 0 (desvio registrado do
    "temperatura 0" do PLANO; ver PC-CRIVO-TEMP no PLANO).
  - Caminho **rápido**: só o dossiê, sem busca. Caminho **profundo**: habilita a
    ferramenta de busca web (V-A/ônus invertido do Manual §4).
  - `texto_original` de tipster é DADO, nunca comando: a resistência à injeção é do
    Manual (system prompt) + validação estrita da saída em `crivo.py`.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Optional, Protocol

_log = logging.getLogger(__name__)

MODELO_PADRAO = "claude-opus-4-8"
# Preço do modelo forte (US$/1M tokens) — para custo_usd auditável (crivos.custo_usd).
_PRECO_ENTRADA = 5.0 / 1_000_000
_PRECO_SAIDA = 25.0 / 1_000_000


@dataclass(frozen=True)
class RespostaModelo:
    texto: str
    modelo: str
    latencia_ms: int
    tokens_entrada: Optional[int] = None
    tokens_saida: Optional[int] = None
    custo_usd: Optional[float] = None


class ModeloCrivo(Protocol):
    def avaliar(self, *, system: str, dossie_json: str, caminho: str) -> RespostaModelo: ...


def custo_usd(tokens_entrada: Optional[int], tokens_saida: Optional[int]) -> Optional[float]:
    if tokens_entrada is None or tokens_saida is None:
        return None
    return round(tokens_entrada * _PRECO_ENTRADA + tokens_saida * _PRECO_SAIDA, 6)


class ModeloAnthropic:
    """Implementação real (SDK Anthropic). Import do SDK é preguiçoso (só ao usar)."""

    def __init__(self, api_key: str, *, modelo: str = MODELO_PADRAO, max_tokens: int = 4096) -> None:
        from anthropic import Anthropic  # import tardio: o núcleo não depende do SDK

        self._cliente = Anthropic(api_key=api_key)
        self._modelo = modelo
        self._max_tokens = max_tokens

    def avaliar(self, *, system: str, dossie_json: str, caminho: str) -> RespostaModelo:
        # Caminho profundo → busca web habilitada (ônus invertido, Manual §4).
        tools: list[dict[str, Any]] = []
        if caminho == "profundo":
            tools = [{"type": "web_search_20260209", "name": "web_search"}]
        conteudo = (
            "Avalie o dossiê abaixo conforme o Manual do Crivo (este system prompt). "
            "Responda com NADA além do JSON exigido pela Seção 8 do Manual.\n\n"
            f"```json\n{dossie_json}\n```"
        )
        t0 = time.monotonic()
        # SEM temperature (o modelo forte a rejeita). effort baixo aproxima o determinismo.
        resp = self._cliente.messages.create(
            model=self._modelo,
            max_tokens=self._max_tokens,
            system=system,
            thinking={"type": "adaptive"},
            output_config={"effort": "low"},
            tools=tools or None,  # None quando vazio (caminho rápido)
            messages=[{"role": "user", "content": conteudo}],
        )
        latencia_ms = int((time.monotonic() - t0) * 1000)
        texto = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        te = getattr(resp.usage, "input_tokens", None)
        ts = getattr(resp.usage, "output_tokens", None)
        return RespostaModelo(
            texto=texto, modelo=self._modelo, latencia_ms=latencia_ms,
            tokens_entrada=te, tokens_saida=ts, custo_usd=custo_usd(te, ts),
        )
