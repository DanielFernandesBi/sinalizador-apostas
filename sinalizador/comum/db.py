"""Cliente Supabase (service role) — ÚnICO ponto de escrita no banco.

Este módulo NASCE sabendo da imutabilidade (Doutrina P7 / schema 0001): as
tabelas de log não aceitam UPDATE nem DELETE, e `sinais`/`apostas`/`tips` só
aceitam UMA transição específica cada. Por isso a API deste módulo NÃO expõe
update genérico nem delete algum — só as operações permitidas. Assim a
imutabilidade do banco nunca vira exceção-surpresa no código de camada: o verbo
proibido simplesmente não existe aqui (regra 7 — não se contorna a imutabilidade;
repensa-se a operação).

Operações expostas:
  - inserir(tabela, registro)              INSERT em tabela append-only
  - pulsar(daemon, detalhe)                heartbeat (INSERT em heartbeats)
  - transicionar_status_sinal(...)         única mutação de `sinais` (a partir de aguardando_crivo)
  - liquidar_aposta(...)                   única mutação de `apostas` (pendente -> final)
  - fechar_tip(...)                        único preenchimento de fechamento em `tips`
  - publicar_config(chave, valor)          publica nova versão vigente de governança (rito)
  - gates_vigentes() / config_vigente() / casa_por_nome()    leituras
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from supabase import Client, create_client

from .config import carregar_config

# Espelham os CHECKs / triggers do schema 0001.
_STATUS_SINAL_PERMITIDOS = {"confirmado", "vetado", "expirado", "erro"}
_RESULTADO_APOSTA_FINAL = {"green", "red", "void", "meio_green", "meio_red"}


class EstadoInesperadoError(RuntimeError):
    """Operação é permitida, mas o registro não estava no estado esperado
    (ex.: sinal já não estava em 'aguardando_crivo'; aposta já liquidada).
    Espelha, no código, a recusa que o trigger faria no banco."""


def _agora_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Banco:
    """Fachada de acesso ao Supabase via service role."""

    def __init__(self, client: Optional[Client] = None) -> None:
        if client is None:
            cfg = carregar_config()
            client = create_client(cfg.supabase_url, cfg.supabase_service_role_key)
        self._c = client

    # ---------------- LEITURA de governança ----------------

    def gates_vigentes(self) -> list[dict[str, Any]]:
        resp = self._c.table("gates").select("*").eq("vigente", True).execute()
        return resp.data or []

    def config_vigente(self, chave: str) -> Optional[dict[str, Any]]:
        """Documento vigente de `config_sistema` (ex.: 'doutrina', 'manual_crivo_l2')."""
        resp = (
            self._c.table("config_sistema")
            .select("*")
            .eq("chave", chave)
            .eq("vigente", True)
            .limit(1)
            .execute()
        )
        dados = resp.data or []
        return dados[0] if dados else None

    def casa_por_nome(self, nome: str) -> Optional[dict[str, Any]]:
        """Casa ativa por nome (fonte da `comissao_pct` para o edge — nunca constante)."""
        resp = (
            self._c.table("casas")
            .select("*")
            .eq("nome", nome)
            .eq("ativa", True)
            .limit(1)
            .execute()
        )
        dados = resp.data or []
        return dados[0] if dados else None

    # ---------------- ESCRITA: apenas o permitido ----------------

    def inserir(self, tabela: str, registro: dict[str, Any]) -> dict[str, Any]:
        """INSERT append-only. Não há update/delete equivalente por desenho."""
        resp = self._c.table(tabela).insert(registro).execute()
        dados = resp.data or []
        return dados[0] if dados else {}

    def pulsar(self, daemon: str, detalhe: Optional[dict[str, Any]] = None) -> None:
        """Heartbeat do daemon (E1.5). É apenas um INSERT em `heartbeats`."""
        self.inserir("heartbeats", {"daemon": daemon, "detalhe": detalhe})

    def transicionar_status_sinal(self, sinal_id: str, novo_status: str) -> dict[str, Any]:
        """Única mutação de `sinais`: muda só `status`, e só a partir de
        'aguardando_crivo' (espelha o trigger tg_sinais_upd)."""
        if novo_status not in _STATUS_SINAL_PERMITIDOS:
            raise ValueError(
                f"status de sinal inválido: {novo_status!r} "
                f"(permitidos: {sorted(_STATUS_SINAL_PERMITIDOS)})"
            )
        resp = (
            self._c.table("sinais")
            .update({"status": novo_status})
            .eq("id", sinal_id)
            .eq("status", "aguardando_crivo")
            .execute()
        )
        atualizados = resp.data or []
        if not atualizados:
            raise EstadoInesperadoError(
                f"sinal {sinal_id} não estava em 'aguardando_crivo' — "
                f"a transição de status é única e só ocorre uma vez"
            )
        return atualizados[0]

    def liquidar_aposta(
        self, aposta_id: str, resultado: str, retorno_liquido: float
    ) -> dict[str, Any]:
        """Única mutação de `apostas`: liquidação única (pendente -> final)."""
        if resultado not in _RESULTADO_APOSTA_FINAL:
            raise ValueError(
                f"resultado de aposta inválido: {resultado!r} "
                f"(permitidos: {sorted(_RESULTADO_APOSTA_FINAL)})"
            )
        resp = (
            self._c.table("apostas")
            .update(
                {
                    "resultado": resultado,
                    "retorno_liquido": retorno_liquido,
                    "liquidada_em": _agora_utc_iso(),
                }
            )
            .eq("id", aposta_id)
            .eq("resultado", "pendente")
            .execute()
        )
        atualizados = resp.data or []
        if not atualizados:
            raise EstadoInesperadoError(
                f"aposta {aposta_id} não estava 'pendente' — a liquidação é única"
            )
        return atualizados[0]

    def fechar_tip(
        self, tip_id: int, odd_fechamento_ref: float, clv_pct: float
    ) -> dict[str, Any]:
        """Único preenchimento de fechamento em `tips` (uma vez só)."""
        resp = (
            self._c.table("tips")
            .update({"odd_fechamento_ref": odd_fechamento_ref, "clv_pct": clv_pct})
            .eq("id", tip_id)
            .is_("odd_fechamento_ref", "null")
            .execute()
        )
        atualizados = resp.data or []
        if not atualizados:
            raise EstadoInesperadoError(
                f"tip {tip_id} já tinha fechamento preenchido — o preenchimento é único"
            )
        return atualizados[0]

    def publicar_config(self, chave: str, valor: str) -> dict[str, Any]:
        """Publica nova versão VIGENTE de um documento de governança (repo → banco).

        Usado SÓ pelo rito (scripts/sync_governanca.py), após aprovação. As versões
        se acumulam (nunca se apaga); a próxima é max(versao)+1. Desvigora a atual
        antes de inserir a nova por causa do índice único parcial `ux_config_vigente`.
        A versão é derivada de max(versao) — não de `vigente` — para ser idempotente
        mesmo após uma falha parcial (estado com 0 vigente).
        """
        resp = (
            self._c.table("config_sistema")
            .select("versao")
            .eq("chave", chave)
            .order("versao", desc=True)
            .limit(1)
            .execute()
        )
        linhas = resp.data or []
        proxima = (linhas[0]["versao"] + 1) if linhas else 1
        self._c.table("config_sistema").update({"vigente": False}).eq(
            "chave", chave
        ).eq("vigente", True).execute()
        return self.inserir(
            "config_sistema",
            {"chave": chave, "versao": proxima, "valor": valor, "vigente": True},
        )
