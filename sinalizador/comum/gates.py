"""Carregador de gates — lê a tabela `gates`, cacheia com TTL e FALHA ALTO.

Regra 6 (CLAUDE.md): valores de gate vêm SEMPRE da tabela, nunca do código. As
únicas constantes numéricas aqui são as *baselines* dos gates PÉTREOS da
Doutrina §4 — usadas como tripwire de integridade, não como valores de operação
(estes continuam vindo da tabela via `get()`).

"Dado ausente = abortar" (Doutrina P6) vale para configuração: em
`validar_integridade()` — chamada na inicialização de todo processo — qualquer
um dos casos abaixo levanta `GateInvalidoError` e o processo NÃO sobe:
  - um dos 9 gates nomeados ausente;
  - flag `petreo` ou `direcao_endurecer` divergente do esperado;
  - um pétreo AFROUXADO abaixo da baseline (proibido pela Doutrina — pétreo só endurece).

Um pétreo ENDURECIDO em relação à baseline (mudança legítima pelo rito, P11) é
aceito, com aviso alto no log — nunca derruba o processo, senão uma decisão de
governança válida quebraria todos os daemons até alguém editar código.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional

from .db import Banco
from .log import get_logger

_log = get_logger(__name__)


@dataclass(frozen=True)
class MetaGate:
    petreo: bool
    direcao: str  # 'menor' = endurecer é diminuir; 'maior' = endurecer é aumentar
    baseline_petreo: Optional[Decimal] = None  # só para pétreos (tripwire Doutrina §4)


# Espelho estrutural do seed do schema 0001 e da Doutrina §4 (com a Sugestão nº 1,
# janela_sincronia_s). Para os gates "a calibrar" (não pétreos) NÃO há baseline:
# seus valores mudam pelo backtest/rito e não podem ser fixados em código.
METADADOS_GATES: dict[str, MetaGate] = {
    "edge_min_pct":            MetaGate(petreo=False, direcao="maior"),
    "odd_teto":                MetaGate(petreo=False, direcao="menor"),
    "liquidez_multiplo_stake": MetaGate(petreo=False, direcao="maior"),
    "snapshot_idade_max_s":    MetaGate(petreo=False, direcao="menor"),
    "janela_sincronia_s":      MetaGate(petreo=False, direcao="menor"),
    "stake_max_pct":           MetaGate(petreo=True,  direcao="menor", baseline_petreo=Decimal("2.0")),
    "kelly_fracao":            MetaGate(petreo=True,  direcao="menor", baseline_petreo=Decimal("0.25")),
    "drawdown_suspensao_pct":  MetaGate(petreo=True,  direcao="menor", baseline_petreo=Decimal("20")),
    "amostra_minima":          MetaGate(petreo=True,  direcao="maior", baseline_petreo=Decimal("200")),
}


class GateInvalidoError(RuntimeError):
    """Integridade dos gates violada — o processo NÃO deve subir."""


class CarregadorGates:
    """Lê os gates vigentes da tabela, valida a integridade e cacheia por TTL curto."""

    def __init__(self, banco: Banco, ttl_segundos: float = 30.0) -> None:
        self._banco = banco
        self._ttl = ttl_segundos
        self._cache: dict[str, Decimal] = {}
        self._carregado_em: float = 0.0

    def validar_integridade(self) -> None:
        """Chamar na inicialização. Levanta GateInvalidoError em qualquer problema."""
        vigentes = {g["nome"]: g for g in self._banco.gates_vigentes()}
        problemas: list[str] = []

        for nome, meta in METADADOS_GATES.items():
            g = vigentes.get(nome)
            if g is None:
                problemas.append(f"gate ausente: {nome!r}")
                continue
            if bool(g.get("petreo")) != meta.petreo:
                problemas.append(
                    f"{nome}: flag 'petreo' divergente "
                    f"(tabela={g.get('petreo')!r}, esperado={meta.petreo!r})"
                )
            if g.get("direcao_endurecer") != meta.direcao:
                problemas.append(
                    f"{nome}: 'direcao_endurecer' divergente "
                    f"(tabela={g.get('direcao_endurecer')!r}, esperado={meta.direcao!r})"
                )
            if meta.petreo and meta.baseline_petreo is not None:
                try:
                    valor = Decimal(str(g["valor"]))
                except (InvalidOperation, KeyError, TypeError):
                    problemas.append(f"{nome}: valor não numérico ({g.get('valor')!r})")
                    continue
                if _mais_frouxo(valor, meta.baseline_petreo, meta.direcao):
                    problemas.append(
                        f"{nome}: pétreo AFROUXADO (tabela={valor}, baseline={meta.baseline_petreo}, "
                        f"endurecer={meta.direcao}) — proibido pela Doutrina §4"
                    )
                elif valor != meta.baseline_petreo:
                    _log.warning(
                        "pétreo endurecido em relação à baseline v0.1 (mudança de rito?)",
                        extra={"gate": nome, "valor": str(valor),
                               "baseline": str(meta.baseline_petreo)},
                    )

        extras = set(vigentes) - set(METADADOS_GATES)
        if extras:
            # Gate vigente sem lastro no código: pode ser evolução por rito. Sinaliza, não derruba.
            _log.warning("gates vigentes não previstos no código", extra={"gates": sorted(extras)})

        if problemas:
            raise GateInvalidoError("; ".join(problemas))

        self._recarregar(vigentes)

    def get(self, nome: str) -> Decimal:
        """Valor vigente do gate (da tabela, com cache TTL). KeyError se nome desconhecido."""
        if nome not in METADADOS_GATES:
            raise KeyError(f"gate desconhecido: {nome!r}")
        if self._expirado():
            self._recarregar()
        try:
            return self._cache[nome]
        except KeyError:
            raise GateInvalidoError(f"gate {nome!r} sumiu da tabela após a carga inicial")

    # ---------------- interno ----------------

    def _expirado(self) -> bool:
        return (time.monotonic() - self._carregado_em) > self._ttl

    def _recarregar(self, vigentes: Optional[dict[str, dict]] = None) -> None:
        if vigentes is None:
            vigentes = {g["nome"]: g for g in self._banco.gates_vigentes()}
        self._cache = {
            nome: Decimal(str(g["valor"]))
            for nome, g in vigentes.items()
            if nome in METADADOS_GATES
        }
        self._carregado_em = time.monotonic()


def _mais_frouxo(valor: Decimal, baseline: Decimal, direcao: str) -> bool:
    """True se `valor` é mais FROUXO que a baseline dado o sentido de endurecimento."""
    if direcao == "menor":       # endurecer = diminuir → mais frouxo = maior
        return valor > baseline
    return valor < baseline      # direcao == 'maior': endurecer = aumentar → mais frouxo = menor
