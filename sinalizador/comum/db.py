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
  - inserir_muitos(tabela, registros)      INSERT em lote (N linhas em 1 POST)
  - pulsar(daemon, detalhe)                heartbeat (INSERT em heartbeats)
  - transicionar_status_sinal(...)         única mutação de `sinais` (a partir de aguardando_crivo)
  - registrar_aposta(...) / liquidar_e_lancar(...)   E5.3/E5.4: TRANSACIONAIS (RPC, migration 0004)
  - fechar_tip(...)                        único preenchimento de fechamento em `tips`
  - marcar_notificacao_entregue(id)        `notificacoes` aceita UPDATE (não é append-only)
  - marcar_evento_encerrado(id)            `eventos.status` (L4: sai da fila de fechamento)
  - publicar_config(chave, valor)          publica nova versão vigente de governança (rito)
  - gates_vigentes() / config_vigente() / casa_por_nome() / exposicao_aberta()    leituras
  - evento_por_id_externo() / saude_daemons() / clv_global()                       leituras
  - snapshots_desde() / casas_ativas() / eventos_por_ids() / banca_atual()          leituras (L1)
  - chaves_sinais_abertos() / chaves_abortos_desde()                               anti-duplicidade (L1)
  - homologacao_mercados()                                                          gate de homologação (L1, P2)
  - sinais_aguardando_crivo()                                                        leitura (L2)
  - sinais_por_status() / crivo_do_sinal() / ultimo_snapshot_venue() /
    notificacoes_do_sinal() / notificacoes_pendentes()                              leituras (L3)
  - eventos_iniciados_sem_status_final() / snapshots_do_evento() /
    sinais_do_evento() / abortos_rastreados_do_evento() / clv_ids_registrados()     leituras (L4)
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any, Optional

from supabase import Client, create_client

from .config import carregar_config
from .dinheiro import centavos, dec, validar_odd, validar_stake

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

    def exposicao_aberta(self) -> list[dict[str, Any]]:
        """Linhas de `vw_exposicao_aberta` (exposto por jogo/liga-dia/dia)."""
        resp = self._c.table("vw_exposicao_aberta").select("*").execute()
        return resp.data or []

    def evento_por_id_externo(self, fonte: str, valor: str) -> Optional[dict[str, Any]]:
        """Evento cujo `ids_externos->>fonte` == valor (upsert do L0 por id da fonte)."""
        resp = (
            self._c.table("eventos")
            .select("*")
            .eq(f"ids_externos->>{fonte}", valor)
            .limit(1)
            .execute()
        )
        dados = resp.data or []
        return dados[0] if dados else None

    def saude_daemons(self) -> list[dict[str, Any]]:
        """Linhas de `vw_saude_daemons` (último pulso e silêncio por daemon) — E1.5."""
        resp = self._c.table("vw_saude_daemons").select("*").execute()
        return resp.data or []

    def clv_global(self) -> list[dict[str, Any]]:
        """Linhas de `vw_clv_global` (n, clv_medio, desvio por real/contrafactual).

        Mantida para auditoria histórica. O RELATÓRIO usa `clv_por_categoria`: o
        booleano `contrafactual` fundiria veto do crivo, near-miss e falha
        operacional numa média só, o que a Doutrina §3 (v0.1.7) proíbe.
        """
        resp = self._c.table("vw_clv_global").select("*").execute()
        return resp.data or []

    def clv_por_categoria(self) -> list[dict[str, Any]]:
        """`vw_clv_por_categoria` — CLV acumulado por categoria de desfecho."""
        resp = self._c.table("vw_clv_por_categoria").select("*").execute()
        return resp.data or []

    def clv_do_dia(self, dia: str) -> list[dict[str, Any]]:
        """`vw_clv_diario` de um dia (data das PARTIDAS, não do cálculo)."""
        resp = self._c.table("vw_clv_diario").select("*").eq("dia", dia).execute()
        return resp.data or []

    def clv_perda_amostra_do_dia(self, dia: str) -> list[dict[str, Any]]:
        """`vw_clv_perda_amostra` de um dia — o que não virou CLV, e por quê."""
        resp = self._c.table("vw_clv_perda_amostra").select("*").eq("dia", dia).execute()
        return resp.data or []

    def snapshots_desde(self, ts_iso: str) -> list[dict[str, Any]]:
        """Snapshots com `ts_captura >= ts_iso` (janela de trabalho do L1)."""
        resp = (
            self._c.table("odds_snapshots")
            .select("*")
            .gte("ts_captura", ts_iso)
            .order("ts_captura")
            .execute()
        )
        return resp.data or []

    def casas_ativas(self) -> list[dict[str, Any]]:
        """Todas as casas ativas (mapa id→tipo/comissão para o L1)."""
        resp = self._c.table("casas").select("*").eq("ativa", True).execute()
        return resp.data or []

    def eventos_por_ids(self, ids: list[str]) -> list[dict[str, Any]]:
        """Eventos pelos ids (liga/partida/início para o dossiê)."""
        if not ids:
            return []
        resp = self._c.table("eventos").select("*").in_("id", ids).execute()
        return resp.data or []

    def evento_por_id(self, evento_id: str) -> Optional[dict[str, Any]]:
        """Um evento pelo id (L3/L4: liga/partida/início para cartão e fechamento)."""
        resp = self._c.table("eventos").select("*").eq("id", evento_id).limit(1).execute()
        dados = resp.data or []
        return dados[0] if dados else None

    def banca_atual(self) -> Optional[dict[str, Any]]:
        """Linha de `vw_banca` (saldo/pico/drawdown/kill_switch). None se sem ledger."""
        resp = self._c.table("vw_banca").select("*").limit(1).execute()
        dados = resp.data or []
        return dados[0] if dados else None

    def chaves_sinais_abertos(self) -> set[str]:
        """Chaves de candidato dos sinais AINDA ABERTOS (aguardando_crivo|confirmado)
        — anti-duplicidade do L1 (achado 7): não reemite candidato com sinal aberto.
        Espelha em leitura o índice único parcial `ux_sinais_candidato_aberto`."""
        resp = (
            self._c.table("sinais")
            .select("chave_candidato")
            .in_("status", ["aguardando_crivo", "confirmado"])
            .execute()
        )
        return {r["chave_candidato"] for r in (resp.data or []) if r.get("chave_candidato")}

    def chaves_abortos_desde(self, ts_iso: str) -> set[str]:
        """Chaves de candidato dos abortos registrados desde `ts_iso` — dedup de
        abortos do L1 (achado 7): não re-registra o mesmo near-miss a cada ciclo."""
        resp = (
            self._c.table("abortos_l1")
            .select("chave_candidato")
            .gte("ts", ts_iso)
            .execute()
        )
        return {r["chave_candidato"] for r in (resp.data or []) if r.get("chave_candidato")}

    def homologacao_mercados(self) -> dict[tuple[str, str], str]:
        """Status de homologação por (liga, mercado) — Doutrina P2 / achado 8.

        Chave canônica: (`eventos.liga`, mercado em {'1x2','ah','ou'} — o mesmo string
        que o L0 grava em `odds_snapshots.mercado`; o comentário do schema 0001
        ('ou_gols') é ilustrativo, não há CHECK em `mercado`). Valor = `status`
        ('backtest'|'homologado'|'suspenso'|'caducado'); `suspenso_em` preenchido força
        'suspenso'. Só 'homologado' autoriza o caminho normal (L2/L3); 'backtest' →
        candidato_sombra (só CLV). A AUSÊNCIA de linha NÃO aparece aqui — o L1 a trata
        como falha de configuração (P2 não autoriza calibração implícita)."""
        resp = (
            self._c.table("mercados_homologados")
            .select("liga,mercado,status,suspenso_em")
            .execute()
        )
        out: dict[tuple[str, str], str] = {}
        for r in (resp.data or []):
            liga, mercado = r.get("liga"), r.get("mercado")
            if not liga or not mercado:
                continue
            status = "suspenso" if r.get("suspenso_em") else (r.get("status") or "")
            out[(str(liga), str(mercado))] = status
        return out

    def sinais_aguardando_crivo(self, limite: int = 50,
                                agora_iso: Optional[str] = None) -> list[dict[str, Any]]:
        """Fila do L2: sinais 'aguardando_crivo' de partidas que AINDA NÃO começaram.

        Com `agora_iso`, usa `fn_fila_crivo` (migration 0008), que exclui no banco os
        sinais cujo evento já iniciou: confirmar depois do apito produziria cartão de
        uma aposta que não existe mais, e disputaria com o `fn_timeout_crivo` do L4
        pelo mesmo sinal. Sem `agora_iso`, mantém o comportamento antigo (usado pela
        varredura de frescor do L3, que precisa ver toda a fila).
        """
        if agora_iso is not None:
            return self._rpc_lista("fn_fila_crivo",
                                   {"p_agora": agora_iso, "p_limite": limite})
        resp = (
            self._c.table("sinais")
            .select("*")
            .eq("status", "aguardando_crivo")
            .order("criado_em")
            .limit(limite)
            .execute()
        )
        return resp.data or []

    def sinais_por_status(self, status: str, limite: int = 200) -> list[dict[str, Any]]:
        """Sinais em um dado status (L3: 'confirmado' para notificar)."""
        resp = (
            self._c.table("sinais")
            .select("*")
            .eq("status", status)
            .order("criado_em")
            .limit(limite)
            .execute()
        )
        return resp.data or []

    def crivo_do_sinal(self, sinal_id: str) -> Optional[dict[str, Any]]:
        """Veredicto do L2 para um sinal (para compor o cartão da notificação)."""
        resp = self._c.table("crivos").select("*").eq("sinal_id", sinal_id).limit(1).execute()
        dados = resp.data or []
        return dados[0] if dados else None

    def ultimo_snapshot_venue(
        self, evento_id: str, casa_id: str, mercado: str, selecao: str, linha: Optional[float]
    ) -> Optional[dict[str, Any]]:
        """Último snapshot da casa/venue para a seleção (re-checagem de preço, E4.2)."""
        q = (
            self._c.table("odds_snapshots")
            .select("*")
            .eq("evento_id", evento_id)
            .eq("casa_id", casa_id)
            .eq("mercado", mercado)
            .eq("selecao", selecao)
        )
        q = q.is_("linha", "null") if linha is None else q.eq("linha", linha)
        resp = q.order("ts_captura", desc=True).limit(1).execute()
        dados = resp.data or []
        return dados[0] if dados else None

    def notificacoes_do_sinal(self, sinal_id: str, tipo: Optional[str] = None) -> list[dict[str, Any]]:
        """Notificações já registradas para um sinal (evita reenvio do cartão)."""
        q = self._c.table("notificacoes").select("*").eq("sinal_id", sinal_id)
        if tipo is not None:
            q = q.eq("tipo", tipo)
        return q.execute().data or []

    def notificacoes_pendentes(self, limite: int = 200) -> list[dict[str, Any]]:
        """Notificações ainda NÃO entregues — leitura, sem reservar (usada só para
        o anti-spam do alerta de drawdown). Quem vai ENVIAR usa
        `reivindicar_notificacoes`, que reserva atomicamente."""
        resp = (
            self._c.table("notificacoes")
            .select("*")
            .in_("status", ["pendente", "enviando"])
            .order("id")
            .limit(limite)
            .execute()
        )
        return resp.data or []

    def reivindicar_notificacoes(self, agora_iso: str, limite: int = 200,
                                 reclaim_s: float = 300.0) -> list[dict[str, Any]]:
        """Reserva atomicamente as notificações a enviar (`fn_reivindicar_notificacoes`).

        Move `pendente → enviando` e devolve as linhas reservadas. Dois processos do L3
        pegam conjuntos DISJUNTOS (`for update skip locked`) — nunca o mesmo cartão.
        Linhas presas em `enviando` por um processo que morreu voltam à fila após
        `reclaim_s`.
        """
        return self._rpc_lista("fn_reivindicar_notificacoes", {
            "p_agora": agora_iso, "p_limite": limite, "p_reclaim_s": reclaim_s,
        })

    def devolver_notificacao(self, notif_id: int) -> bool:
        """Falha de envio: devolve a linha à fila imediatamente (retry no próximo
        ciclo, sem esperar a janela de recuperação)."""
        r = self._rpc("fn_devolver_notificacao", {"p_id": notif_id})
        return bool(r.get("retorno"))

    # ---------------- LEITURA do L4 (fechamento / CLV) ----------------

    def eventos_iniciados_sem_status_final(self, ate_iso: str, limite: int = 200) -> list[dict[str, Any]]:
        """Eventos cujo início já passou (`inicio_utc <= ate_iso`) e ainda não estão
        'encerrado' — candidatos ao fechamento de CLV (L4)."""
        resp = (
            self._c.table("eventos")
            .select("*")
            .lte("inicio_utc", ate_iso)
            .neq("status", "encerrado")
            .order("inicio_utc")
            .limit(limite)
            .execute()
        )
        return resp.data or []

    def snapshots_do_evento(
        self, evento_id: str, casa_ids: Optional[list[str]] = None, ate_iso: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """Snapshots de um evento (opcionalmente só de certas casas e até um `ts_fonte`).
        Base do fechamento: a linha de fechamento é o ÚLTIMO snapshot da referência
        antes do início (L4)."""
        q = self._c.table("odds_snapshots").select("*").eq("evento_id", evento_id)
        if casa_ids is not None:
            q = q.in_("casa_id", casa_ids)
        if ate_iso is not None:
            q = q.lte("ts_fonte", ate_iso)
        return q.order("ts_fonte").execute().data or []

    def sinais_do_evento(self, evento_id: str, status: Optional[list[str]] = None) -> list[dict[str, Any]]:
        """Sinais de um evento (para o CLV: confirmados = real; vetados = contrafactual)."""
        q = self._c.table("sinais").select("*").eq("evento_id", evento_id)
        if status is not None:
            q = q.in_("status", status)
        return q.execute().data or []

    def abortos_rastreados_do_evento(self, evento_id: str) -> list[dict[str, Any]]:
        """Abortos near-miss (`clv_rastrear=true`) de um evento (CLV contrafactual)."""
        resp = (
            self._c.table("abortos_l1")
            .select("*")
            .eq("evento_id", evento_id)
            .eq("clv_rastrear", True)
            .execute()
        )
        return resp.data or []

    def clv_ids_registrados(
        self, *, sinal_ids: Sequence[str] = (), aborto_ids: Sequence[int] = ()
    ) -> tuple[set, set]:
        """Quais destes ids JÁ têm CLV — caminho rápido do fechamento (achado #2.1).

        Recebe os ids do EVENTO em fechamento e pergunta só por eles (`in_`), em vez
        de varrer `clv_log` inteira: a versão anterior recebia `evento_id` e o
        IGNORAVA, lendo a tabela toda a cada evento, a cada ciclo — e `clv_log` só
        cresce (a meta é ≥200 amostras por célula liga×mercado).

        Lista vazia não vira consulta (nem ida à rede). Isto é OTIMIZAÇÃO, não a
        garantia: a unicidade real é dos índices `ux_clv_sinal`/`ux_clv_aborto`
        (migration 0005), que barram a corrida entre esta leitura e o INSERT.
        """
        com_sinal: set = set()
        com_aborto: set = set()
        if sinal_ids:
            resp = (
                self._c.table("clv_log")
                .select("sinal_id")
                .in_("sinal_id", list(sinal_ids))
                .execute()
            )
            com_sinal = {r["sinal_id"] for r in (resp.data or []) if r.get("sinal_id")}
        if aborto_ids:
            resp = (
                self._c.table("clv_log")
                .select("aborto_l1_id")
                .in_("aborto_l1_id", list(aborto_ids))
                .execute()
            )
            com_aborto = {r["aborto_l1_id"] for r in (resp.data or []) if r.get("aborto_l1_id")}
        return com_sinal, com_aborto

    # ---------------- L4: desfecho terminal por item (Sugestão nº 10) ----------------

    def fila_fechamento(self, agora_iso: str, assentamento_s: float,
                        limite: int = 200) -> list[dict[str, Any]]:
        """Eventos com item de CLV AINDA sem desfecho, cujo início + assentamento já
        passou (`fn_fila_fechamento`, migration 0007).

        Substitui "todo evento iniciado com status <> encerrado", que sofria de dois
        males: evento sem book nenhum voltava para sempre (retry infinito) e partidas
        sem item algum ocupavam o `limit`, expulsando eventos novos (starvation).
        """
        return self._rpc_lista("fn_fila_fechamento", {
            "p_agora": agora_iso, "p_assentamento_s": assentamento_s, "p_limite": limite,
        })

    def marcar_timeout_crivo(self, agora_iso: str) -> int:
        """Sinais ainda em `aguardando_crivo` com a partida já iniciada viram
        `timeout_crivo` (`fn_timeout_crivo`). Nenhum sinal pode sobreviver ao kickoff
        sem desfecho: senão o item nunca termina e o evento nunca finaliza."""
        r = self._rpc("fn_timeout_crivo", {"p_agora": agora_iso})
        return int(r.get("retorno") or 0)

    def itens_com_desfecho(self, evento_id: str) -> tuple[set, set]:
        """(sinal_ids, aborto_ids) do evento que JÁ têm desfecho terminal — caminho
        rápido; a garantia é `ux_clv_resultado_*`."""
        resp = (
            self._c.table("clv_resultados")
            .select("sinal_id,aborto_l1_id")
            .eq("evento_id", evento_id)
            .execute()
        )
        linhas = resp.data or []
        return ({r["sinal_id"] for r in linhas if r.get("sinal_id")},
                {r["aborto_l1_id"] for r in linhas if r.get("aborto_l1_id")})

    def registrar_resultado_clv(self, **campos: Any) -> dict[str, Any]:
        """Desfecho terminal de UM item, atômico (`fn_registrar_resultado_clv`).

        Quando `calculado`, grava a medição em `clv_log` e o desfecho em
        `clv_resultados` na MESMA transação — medição sem desfecho (ou o contrário)
        reabriria o buraco que este item fecha. Devolve `registrado=False` se o item
        já tinha desfecho (corrida entre processos do L4), sem erro.
        """
        return self._rpc("fn_registrar_resultado_clv", campos)

    def finalizar_evento_clv(self, evento_id: str,
                             detalhe: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Finaliza o evento no L4 (`fn_finalizar_evento_clv`). O BANCO recusa se
        ainda houver item esperado sem desfecho — o app não decide sozinho que acabou."""
        return self._rpc("fn_finalizar_evento_clv", {
            "p_evento_id": evento_id, "p_detalhe": detalhe or {},
        })

    def marcar_evento_encerrado(self, evento_id: str) -> None:
        """`eventos.status` não é imutável no schema — o fechamento marca 'encerrado'
        para o evento sair da fila do L4."""
        self._c.table("eventos").update({"status": "encerrado"}).eq("id", evento_id).execute()

    def registrar_aposta(
        self, *, sinal_id: str, casa_id: str, odd_executada: Any, stake_valor: Any
    ) -> dict[str, Any]:
        """E5.3 — registra a execução humana: aposta + débito do STAKE no ledger,
        em UMA TRANSAÇÃO (`fn_registrar_aposta`, migration 0004).

        Antes eram dois POSTs separados: falha entre eles deixava a banca
        inconsistente — e a imutabilidade (P7) impede reparar com UPDATE/DELETE. O
        saldo é lido DENTRO da transação (com lock), não pelo app antes de chamar
        (TOCTOU). Devolve {'aposta': {...}, 'saldo': novo_saldo}.
        """
        validar_odd(odd_executada)
        validar_stake(stake_valor)
        return self._rpc("fn_registrar_aposta", {
            "p_sinal_id": sinal_id, "p_casa_id": casa_id,
            "p_odd": str(dec(odd_executada)), "p_stake": str(centavos(stake_valor)),
        })

    def liquidar_e_lancar(self, *, aposta_id: str, resultado: str) -> dict[str, Any]:
        """E5.4 — liquida a aposta + crédito do PAYOUT BRUTO no ledger, em UMA
        TRANSAÇÃO (`fn_liquidar_aposta`, migration 0004).

        O payout é DERIVADO no banco de (resultado, stake, odd) — nunca digitado: o
        valor manual era a origem do erro contábil (debitava-se o stake e creditava-se
        só o lucro, de modo que uma green de R$20 @ 2,05 movia a banca +R$1 em vez de
        +R$21). Devolve {'payout_bruto', 'lucro_liquido', 'saldo', ...}. Recusa (erro
        do banco) se a aposta já estiver liquidada — a liquidação é única.
        """
        if resultado not in _RESULTADO_APOSTA_FINAL:
            raise ValueError(
                f"resultado de aposta inválido: {resultado!r} "
                f"(permitidos: {sorted(_RESULTADO_APOSTA_FINAL)})"
            )
        return self._rpc("fn_liquidar_aposta", {
            "p_aposta_id": aposta_id, "p_resultado": resultado,
        })

    # ---------------- ESCRITA: apenas o permitido ----------------

    def _rpc(self, funcao: str, params: dict[str, Any]) -> dict[str, Any]:
        """Chama uma função SQL (RPC). É assim que as operações TRANSACIONAIS da
        banca acontecem: o Postgres executa registro/liquidação inteiros dentro de
        UMA transação — ou grava tudo, ou nada (migration 0004). Um erro levantado
        pela função (stake ≤ 0, aposta já liquidada, violação de idempotência)
        desfaz a transação e sobe como exceção aqui."""
        resp = self._c.rpc(funcao, params).execute()
        return resp.data if isinstance(resp.data, dict) else {"retorno": resp.data}

    def _rpc_lista(self, funcao: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """RPC que devolve CONJUNTO de linhas (`returns setof`)."""
        resp = self._c.rpc(funcao, params).execute()
        return resp.data if isinstance(resp.data, list) else []

    def registrar_sinal(self, registro: dict[str, Any]) -> dict[str, Any]:
        """Cria um sinal com as travas do banco (`fn_registrar_sinal`, migration 0012).

        A função TRAVA a linha do evento e recusa se a partida já começou ou se o L4
        já finalizou o CLV do evento — a checagem e o INSERT ficam na mesma transação,
        porque entre o `if` do Python e o INSERT o relógio anda (P0.1). Devolve
        `criado=False` quando o candidato já existe (P0.5), sem erro.
        """
        return self._rpc("fn_registrar_sinal", {"p_dados": registro})

    def registrar_aborto(self, registro: dict[str, Any]) -> dict[str, Any]:
        """Cria um aborto/candidato_sombra com as mesmas travas (`fn_registrar_aborto`).
        `criado=False` quando o candidato já foi registrado no evento (P0.6)."""
        return self._rpc("fn_registrar_aborto", {"p_dados": registro})

    def inserir(self, tabela: str, registro: dict[str, Any]) -> dict[str, Any]:
        """INSERT append-only. Não há update/delete equivalente por desenho."""
        resp = self._c.table(tabela).insert(registro).execute()
        dados = resp.data or []
        return dados[0] if dados else {}

    def inserir_muitos(self, tabela: str, registros: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """INSERT append-only em LOTE: N linhas em UM POST (higiene de saída —
        corta um ciclo de captura de dezenas de segundos para poucos). Lista vazia
        é no-op (não faz chamada de rede)."""
        if not registros:
            return []
        resp = self._c.table(tabela).insert(registros).execute()
        return resp.data or []

    def pulsar(self, daemon: str, detalhe: Optional[dict[str, Any]] = None) -> None:
        """Heartbeat do daemon (E1.5). É apenas um INSERT em `heartbeats`."""
        self.inserir("heartbeats", {"daemon": daemon, "detalhe": detalhe})

    def marcar_notificacao_entregue(self, notif_id: int,
                                    agora_iso: Optional[str] = None) -> bool:
        """Fecha a outbox: `enviando → entregue` (`fn_marcar_notificacao_entregue`).

        É o passo imediatamente posterior ao aceite do Telegram. A janela entre os
        dois é irredutível (a API não tem chave de idempotência em `sendMessage`) —
        e a escolha assumida é a duplicata rara em vez do sinal perdido em silêncio.
        """
        r = self._rpc("fn_marcar_notificacao_entregue", {
            "p_id": notif_id, "p_agora": agora_iso or _agora_utc_iso(),
        })
        return bool(r.get("retorno"))

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

    # `liquidar_aposta` (UPDATE cru em `apostas`) foi REMOVIDO pela migration 0004:
    # liquidar sem lançar no ledger, ou lançar sem liquidar, é justamente a falha
    # parcial que corrompia a banca. A liquidação só existe como transação completa —
    # `liquidar_e_lancar`. O verbo perigoso simplesmente não existe aqui (regra 7).

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
