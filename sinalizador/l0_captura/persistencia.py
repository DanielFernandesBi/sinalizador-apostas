"""Persistência do L0 — só o que o `Banco` permite (INSERT append-only + leituras).

`odds_snapshots` é imutável (schema 0001): cada tick é um INSERT novo, com o
carimbo `ts_fonte` vindo da API — o histórico começa a valer no primeiro tick
(E1 aceite #3). Eventos e casas são "get-or-create": lê pelo identificador
estável e insere só se ausente (nunca há UPDATE — o schema o proíbe nessas
tabelas via ausência de mutação no `Banco`).

Depende apenas dos métodos do Banco (Protocol) — testável com fake, sem Supabase.
"""
from __future__ import annotations

import logging
from typing import Any, Optional, Protocol

_log = logging.getLogger(__name__)


class BancoL0(Protocol):
    def garantir_evento(self, dados: dict[str, Any]) -> dict[str, Any]: ...
    def casa_por_nome(self, nome: str) -> Optional[dict[str, Any]]: ...
    def inserir(self, tabela: str, registro: dict[str, Any]) -> dict[str, Any]: ...
    def inserir_muitos(self, tabela: str, registros: list[dict[str, Any]]) -> list[dict[str, Any]]: ...
    def pulsar(self, daemon: str, detalhe: Optional[dict[str, Any]] = None) -> None: ...


def garantir_evento(banco: BancoL0, ev_norm: dict[str, Any]) -> Optional[str]:
    """id do evento no banco, pela RPC transacional `fn_garantir_evento` (P1.2).

    A fonte GOVERNA os fatos do evento: se o kickoff mudou, o banco muda junto e a
    alteração vira uma linha de `eventos_revisoes`. O antigo get-or-create em duas
    etapas (lê, e insere se não achou) tinha dois furos: sem índice único, dois
    ciclos concorrentes criavam duas linhas para a mesma partida; e nada nunca
    atualizava o kickoff, que é a pedra em que a trava de apito do L1, a fila do L4 e
    a linha de fechamento se apoiam.

    None quando o evento veio sem id da fonte ou sem início (dado incompleto — não se
    inventa chave nem horário).
    """
    r = banco.garantir_evento(ev_norm)
    evento_id = r.get("id")
    if not evento_id:
        _log.warning("evento descartado pelo banco (P6)",
                     extra={"motivo": r.get("motivo"), "liga": ev_norm.get("liga")})
        return None
    if r.get("alterado"):
        # Remarcação é fato operacional, não ruído: o L4 vai tirar da amostra tudo o
        # que foi emitido antes dela.
        nivel = _log.warning if r.get("tipo") == "remarcado" else _log.info
        nivel("evento revisado pela fonte", extra={
            "evento_id": evento_id, "tipo": r.get("tipo"), "campos": r.get("campos"),
            "posicoes_papel_liberadas": r.get("posicoes_papel_liberadas"),
        })
    return str(evento_id)


def garantir_casa(
    banco: BancoL0, nome: str, *, tipo: str, comissao_pct: float = 0.0, cache: dict[str, str]
) -> Optional[str]:
    """id da casa (get-or-create por nome), com cache por ciclo. `tipo` e
    `comissao_pct` só são usados ao CRIAR uma casa nova (a existente é imutável:
    o schema não permite UPDATE — regra 7). A classificação (referência/exchange/
    varejo) vem de `mapeamento.classificar_casa` (Sugestão nº 6)."""
    if nome in cache:
        return cache[nome]
    existente = banco.casa_por_nome(nome)
    if existente:
        cache[nome] = existente["id"]
        return existente["id"]
    criado = banco.inserir("casas", {"nome": nome, "tipo": tipo, "comissao_pct": comissao_pct})
    novo_id = criado.get("id")
    if novo_id:
        cache[nome] = novo_id
        _log.info("casa nova registrada", extra={"casa": nome, "tipo": tipo, "comissao_pct": comissao_pct})
    return novo_id


def linha_snapshot(*, evento_id: str, casa_id: str, snap: dict[str, Any]) -> dict[str, Any]:
    """Monta a linha de `odds_snapshots` (sem inserir). `ts_fonte` vem da API
    (nunca relógio local); `ts_captura` é deixado ao default do schema (now())."""
    return {
        "evento_id": evento_id,
        "casa_id": casa_id,
        "mercado": snap["mercado"],
        "selecao": snap["selecao"],
        "linha": snap.get("linha"),
        "odd": snap["odd"],
        "liquidez": snap.get("liquidez"),  # None p/ referência/varejo/exchange-proxy (sem book pela API; E1.2)
        "ts_fonte": snap["ts_fonte"],
        "raw": snap.get("raw"),
    }


def gravar_snapshot(
    banco: BancoL0, *, evento_id: str, casa_id: str, snap: dict[str, Any]
) -> dict[str, Any]:
    """INSERT único em `odds_snapshots` (usado em teste; o ciclo grava em lote)."""
    return banco.inserir("odds_snapshots", linha_snapshot(evento_id=evento_id, casa_id=casa_id, snap=snap))
