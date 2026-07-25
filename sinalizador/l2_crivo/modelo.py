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
# Leitura de cache custa ~0,1× a entrada; escrita de cache, ~1,25× (TTL de 5 min).
_FATOR_CACHE_LEITURA = 0.1
_FATOR_CACHE_ESCRITA = 1.25

# `max_tokens` do caminho profundo. 4096 era apertado: o pensamento adaptativo e a
# resposta DIVIDEM esse teto, então uma análise longa terminava em `max_tokens` com
# o JSON cortado — que a validação lia como saída inválida e virava `erro`
# PERMANENTE, queimando o candidato (P1.7).
MAX_TOKENS_PADRAO = 16000
# Teto de continuações de `pause_turn`. Cada uma é uma chamada nova e paga; sem teto,
# uma busca que nunca converge viraria custo aberto.
MAX_CONTINUACOES = 3
# Teto de buscas web por avaliação — limita custo e a chance de `pause_turn`.
MAX_BUSCAS_PADRAO = 8


class RespostaIncompletaError(RuntimeError):
    """O modelo respondeu, mas a resposta não CONCLUIU (`pause_turn` esgotado ou
    `max_tokens`). Não é juízo sobre o sinal e não é saída inválida: é a análise que
    não terminou. Tratada como transitória — o candidato não pode ser queimado por
    isso (Sugestão nº 11); o kickoff limita o retry via `timeout_crivo`."""

    def __init__(self, motivo: str) -> None:
        super().__init__(motivo)
        self.motivo = motivo


class RecusaDoModeloError(RuntimeError):
    """`stop_reason == 'refusal'`: os classificadores de segurança recusaram. É
    PERMANENTE — reenviar o mesmo conteúdo recusa de novo, então retentar só gasta."""

    def __init__(self, categoria: Optional[str]) -> None:
        super().__init__(f"recusa do modelo (categoria={categoria})")
        self.categoria = categoria


@dataclass(frozen=True)
class RespostaModelo:
    texto: str
    modelo: str
    latencia_ms: int
    tokens_entrada: Optional[int] = None
    tokens_saida: Optional[int] = None
    custo_usd: Optional[float] = None
    # P1.7 — o que a chamada realmente fez. Antes nada disso era observável: o
    # `stop_reason` sequer era lido, e três desfechos distintos (`pause_turn`,
    # `max_tokens`, `refusal`) chegavam como texto que falhava na validação.
    stop_reason: Optional[str] = None
    tokens_cache_leitura: Optional[int] = None
    tokens_cache_escrita: Optional[int] = None
    buscas_web: int = 0
    continuacoes: int = 0


class ModeloCrivo(Protocol):
    def avaliar(self, *, system: str, dossie_json: str, caminho: str) -> RespostaModelo: ...


def custo_usd(tokens_entrada: Optional[int], tokens_saida: Optional[int], *,
              cache_leitura: Optional[int] = None,
              cache_escrita: Optional[int] = None) -> Optional[float]:
    """Custo em US$ da chamada. `input_tokens` é só o resto NÃO cacheado: somar
    apenas ele subestimava a conta sempre que o Manual (system prompt) era servido do
    cache. NÃO inclui a cobrança por USO da busca web, que é cobrada à parte dos
    tokens — o número de buscas fica em `buscas_web` para essa conta ser feita quando
    o preço for confirmado (ver PC-CUSTO-FERRAMENTA)."""
    if tokens_entrada is None or tokens_saida is None:
        return None
    entrada = (tokens_entrada
               + _FATOR_CACHE_LEITURA * (cache_leitura or 0)
               + _FATOR_CACHE_ESCRITA * (cache_escrita or 0))
    return round(entrada * _PRECO_ENTRADA + tokens_saida * _PRECO_SAIDA, 6)


class ModeloAnthropic:
    """Implementação real (SDK Anthropic). Import do SDK é preguiçoso (só ao usar)."""

    def __init__(self, api_key: str, *, modelo: str = MODELO_PADRAO,
                 max_tokens: int = MAX_TOKENS_PADRAO,
                 max_buscas: int = MAX_BUSCAS_PADRAO,
                 max_continuacoes: int = MAX_CONTINUACOES) -> None:
        from anthropic import Anthropic  # import tardio: o núcleo não depende do SDK

        self._cliente = Anthropic(api_key=api_key)
        self._modelo = modelo
        self._max_tokens = max_tokens
        self._max_buscas = max_buscas
        self._max_continuacoes = max_continuacoes

    def avaliar(self, *, system: str, dossie_json: str, caminho: str) -> RespostaModelo:
        """Uma avaliação, incluindo as continuações que a busca web exigir (P1.7).

        `stop_reason` é LIDO e decide o que fazer — antes ele era ignorado, e três
        desfechos distintos chegavam ao chamador como texto que falhava na validação
        e virava `erro` permanente, queimando o candidato:

          - `pause_turn`: o laço server-side da ferramenta bateu o limite de iterações
            e a resposta está pela metade. A API espera que se reenvie a mensagem do
            usuário MAIS a resposta do assistente para continuar de onde parou (não se
            acrescenta um "continue": a própria API detecta o bloco de ferramenta
            pendente). Aqui isso vira laço, com teto.
          - `max_tokens`: resposta cortada. Não é saída inválida, é análise inacabada.
          - `refusal`: os classificadores recusaram; permanente.
        """
        # Caminho profundo → busca web habilitada (ônus invertido, Manual §4).
        tools: list[dict[str, Any]] = []
        if caminho == "profundo":
            tools = [{"type": "web_search_20260209", "name": "web_search",
                      "max_uses": self._max_buscas}]
        conteudo = (
            "Avalie o dossiê abaixo conforme o Manual do Crivo (este system prompt). "
            "Responda com NADA além do JSON exigido pela Seção 8 do Manual.\n\n"
            f"```json\n{dossie_json}\n```"
        )
        mensagens: list[dict[str, Any]] = [{"role": "user", "content": conteudo}]

        t0 = time.monotonic()
        partes: list[str] = []
        soma = {"entrada": 0, "saida": 0, "cache_leitura": 0, "cache_escrita": 0}
        buscas = continuacoes = 0
        stop_reason: Optional[str] = None

        for tentativa in range(self._max_continuacoes + 1):
            # SEM temperature (o modelo forte a rejeita); effort baixo aproxima o
            # determinismo.
            resp = self._cliente.messages.create(
                model=self._modelo,
                max_tokens=self._max_tokens,
                system=system,
                thinking={"type": "adaptive"},
                output_config={"effort": "low"},
                tools=tools or None,          # None quando vazio (caminho rápido)
                messages=mensagens,
            )
            stop_reason = getattr(resp, "stop_reason", None)
            _somar_uso(soma, getattr(resp, "usage", None))
            partes.append("".join(b.text for b in resp.content
                                  if getattr(b, "type", None) == "text"))
            buscas += sum(1 for b in resp.content
                          if getattr(b, "type", None) == "server_tool_use")

            if stop_reason != "pause_turn":
                break
            continuacoes = tentativa + 1
            # Continua a MESMA conversa: usuário + o que o assistente produziu.
            mensagens = [{"role": "user", "content": conteudo},
                         {"role": "assistant", "content": resp.content}]

        latencia_ms = int((time.monotonic() - t0) * 1000)
        resposta = RespostaModelo(
            texto="".join(partes), modelo=self._modelo, latencia_ms=latencia_ms,
            tokens_entrada=soma["entrada"], tokens_saida=soma["saida"],
            custo_usd=custo_usd(soma["entrada"], soma["saida"],
                                cache_leitura=soma["cache_leitura"],
                                cache_escrita=soma["cache_escrita"]),
            stop_reason=stop_reason,
            tokens_cache_leitura=soma["cache_leitura"],
            tokens_cache_escrita=soma["cache_escrita"],
            buscas_web=buscas, continuacoes=continuacoes,
        )

        if stop_reason == "refusal":
            categoria = getattr(getattr(resp, "stop_details", None), "category", None)
            _log.warning("modelo recusou a avaliação", extra={"categoria": categoria})
            raise RecusaDoModeloError(categoria)
        if stop_reason == "pause_turn":
            raise RespostaIncompletaError(
                f"pause_turn ainda pendente após {self._max_continuacoes} continuações")
        if stop_reason == "max_tokens":
            raise RespostaIncompletaError(
                f"resposta cortada em max_tokens={self._max_tokens}")
        return resposta


def _somar_uso(soma: dict[str, int], usage: Any) -> None:
    """Acumula o `usage` de uma chamada. Cada continuação de `pause_turn` é uma
    chamada nova e PAGA: reportar só a última subestimaria o custo do caminho
    profundo justamente onde ele é maior."""
    if usage is None:
        return
    soma["entrada"] += int(getattr(usage, "input_tokens", 0) or 0)
    soma["saida"] += int(getattr(usage, "output_tokens", 0) or 0)
    soma["cache_leitura"] += int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    soma["cache_escrita"] += int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
