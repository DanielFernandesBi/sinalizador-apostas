"""E1.5 — Vigia de heartbeats: silêncio de daemon → alerta.

Cada daemon pulsa a cada ciclo (`banco.pulsar`); o vigia lê `vw_saude_daemons` e,
para todo daemon esperado em silêncio além do limiar (ou que nunca pulsou),
registra uma `notificacao` do tipo `alerta_daemon`. A ENTREGA (Telegram) é do L3
(E4.3) — aqui só se grava a notificação (entregue=false), no padrão do sistema.

O limiar de silêncio é parâmetro OPERACIONAL (depende da cadência de captura, que
no tier gratuito é baixa) — não é gate da Doutrina, por isso vive aqui e é
configurável, não na tabela `gates`.
"""
from __future__ import annotations

import logging
from typing import Any, Optional, Protocol

_log = logging.getLogger(__name__)

# Padrão folgado (2h): no tier gratuito a cadência é baixa. Ajustar por ambiente.
LIMIAR_SILENCIO_S_PADRAO = 7200.0

DAEMONS_ESPERADOS_PADRAO = ("l0_referencia", "l0_varejo")


class BancoVigia(Protocol):
    def saude_daemons(self) -> list[dict[str, Any]]: ...
    def inserir(self, tabela: str, registro: dict[str, Any]) -> dict[str, Any]: ...


def daemons_mudos(
    banco: BancoVigia,
    *,
    limiar_s: float = LIMIAR_SILENCIO_S_PADRAO,
    esperados=DAEMONS_ESPERADOS_PADRAO,
) -> list[dict[str, Any]]:
    """Lista de daemons em silêncio além do limiar (ou que nunca pulsaram)."""
    saude = {r["daemon"]: r for r in banco.saude_daemons()}
    mudos: list[dict[str, Any]] = []
    for nome in esperados:
        linha = saude.get(nome)
        if linha is None:
            mudos.append({"daemon": nome, "segundos": None, "motivo": "nunca pulsou"})
            continue
        segundos = linha.get("segundos_em_silencio")
        if segundos is not None and float(segundos) > limiar_s:
            mudos.append({"daemon": nome, "segundos": float(segundos),
                          "motivo": f"silêncio {float(segundos):.0f}s > limiar {limiar_s:.0f}s"})
    return mudos


def _conteudo(m: dict[str, Any]) -> str:
    return f"[alerta_daemon] {m['daemon']}: {m['motivo']}"


def alertar_mudos(banco: BancoVigia, mudos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Registra uma `notificacao` alerta_daemon por daemon mudo. Entrega é do L3."""
    inseridas: list[dict[str, Any]] = []
    for m in mudos:
        reg = banco.inserir(
            "notificacoes",
            {"sinal_id": None, "tipo": "alerta_daemon", "canal": "telegram",
             "conteudo": _conteudo(m), "entregue": False},
        )
        inseridas.append(reg)
        _log.warning("daemon mudo — alerta registrado", extra=m)
    return inseridas


def rodar_vigia(
    banco: BancoVigia,
    *,
    limiar_s: float = LIMIAR_SILENCIO_S_PADRAO,
    esperados=DAEMONS_ESPERADOS_PADRAO,
) -> list[dict[str, Any]]:
    """Uma passada do vigia: detecta e alerta. Devolve os daemons mudos encontrados."""
    mudos = daemons_mudos(banco, limiar_s=limiar_s, esperados=esperados)
    if mudos:
        alertar_mudos(banco, mudos)
    return mudos
