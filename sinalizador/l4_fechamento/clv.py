"""E5.1/E5.2 — linha de fechamento (Pinnacle de-vigada) e cômputo do CLV.

CLV de um sinal (Doutrina §3): a odd capturada na emissão vs a linha de fechamento
da referência sharp, em probabilidade. Aqui:

    p_fechamento = prob JUSTA da referência no fechamento (de-vig Shin de TODAS as
                   seleções do mercado no último snapshot antes do início);
    clv_pct      = (odd_emissao × p_fechamento − 1) × 100   ("bateu o fechamento?")
                   > 0 → a odd da emissão era melhor que a linha justa de fecho.

Mercado com book de referência incompleto no fechamento → sem de-vig → sem CLV
(P6: não se inventa). Sinais confirmados dão CLV real; vetados/abortos dão CLV
`contrafactual` (auditoria do crivo — vw_clv_por_veto).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from sinalizador.comum.erros import e_violacao_unicidade
from sinalizador.comum.tempo import para_datetime
from sinalizador.l1_gatilhos.devig import devig_shin
from sinalizador.l1_gatilhos.orquestrador import ORDEM_SELECAO

_log = logging.getLogger(__name__)

DAEMON = "l4"


def _linha_key(linha: Any) -> Optional[float]:
    if linha is None:
        return None
    try:
        return round(float(linha), 2)
    except (TypeError, ValueError):
        return None


def prob_implicita(odd: float) -> float:
    return 1.0 / float(odd)


def clv_pct(odd_emissao: float, p_fechamento: float) -> float:
    """> 0 se a odd da emissão bate a linha justa de fechamento."""
    return (float(odd_emissao) * float(p_fechamento) - 1.0) * 100.0


@dataclass(frozen=True)
class RevisaoFechamento:
    """A linha de fechamento de um (mercado, linha): UMA revisão, indivisível."""
    casa_id: str
    mercado: str
    linha: Optional[float]
    ts_fonte: datetime          # carimbo REAL da revisão usada (vai para clv_log)
    idade_s: float              # início − ts_fonte (quão antes do kickoff)
    probs: dict[str, float]     # seleção → p_justa (Shin sobre ESTA revisão)


@dataclass(frozen=True)
class FechamentoIndisponivel:
    """Por que um (mercado, linha) ficou sem linha de fechamento. NUNCA silencioso:
    a ausência tende a se concentrar em mercados voláteis/suspensos perto do início,
    então some enviesada — e é preciso poder medir quanto se perdeu e onde."""
    mercado: str
    linha: Optional[float]
    motivo: str                 # 'sem_revisao_completa' | 'revisao_completa_defasada'
    limite_s: float
    idade_s: Optional[float] = None


def _agrupar_por_revisao(
    snaps_ref: list[dict[str, Any]]
) -> dict[tuple, dict[str, float]]:
    """Agrupa os snapshots pela unidade INDIVISÍVEL da revisão:
    `(casa_id, mercado, linha canônica, ts_fonte)` → {seleção: odd}.

    `casa_id` entra na chave de propósito: se houver mais de uma casa de referência,
    o book jamais pode ser montado juntando seleções de referências distintas.
    """
    revisoes: dict[tuple, dict[str, float]] = {}
    for s in snaps_ref:
        if s.get("odd") is None:
            continue
        ts = para_datetime(s.get("ts_fonte"))
        if ts is None:
            continue  # sem carimbo não há revisão a que pertencer (P6)
        chave = (s.get("casa_id"), s["mercado"], _linha_key(s.get("linha")), ts)
        revisoes.setdefault(chave, {})[s["selecao"]] = float(s["odd"])
    return revisoes


def revisoes_de_fechamento(
    snaps_ref: list[dict[str, Any]], *, inicio: datetime, limite_idade_s: float
) -> tuple[dict[tuple, RevisaoFechamento], list[FechamentoIndisponivel]]:
    """Escolhe a linha de fechamento de cada (mercado, linha) — Doutrina §3 (Sugestão nº 9).

    A linha de fechamento é a **revisão completa mais recente** anterior ou igual ao
    início, desde que sua defasagem não exceda `fechamento_idade_max_s`.

    NÃO se começa pegando o último preço de cada seleção: isso montaria um book que
    nunca existiu (preço fresco de duas seleções casado com preço velho da terceira,
    o que acontece sempre que a revisão mais recente omite uma seleção — suspensão
    perto do início). A revisão é indivisível: completa ou descartada inteira.

    Devolve (fechamentos, indisponíveis). Mercado fora do escopo (`ORDEM_SELECAO`)
    não entra em nenhuma das duas listas — não é dado faltando, é fora de escopo.
    """
    revisoes = _agrupar_por_revisao(snaps_ref)

    # Candidatas por (mercado, linha): completas e anteriores/iguais ao início.
    completas: dict[tuple, list[tuple[datetime, str, dict[str, float]]]] = {}
    vistos: set[tuple] = set()          # (mercado, linha) que existem nos snapshots
    for (casa_id, mercado, linha, ts), odds_por_sel in revisoes.items():
        ordem = ORDEM_SELECAO.get(mercado)
        if ordem is None:
            continue                     # fora do escopo — silencioso, não é falta de dado
        vistos.add((mercado, linha))
        if ts > inicio:
            continue                     # revisão posterior ao início não é fechamento
        if any(sel not in odds_por_sel for sel in ordem):
            continue                     # incompleta: descartada INTEIRA (nunca completada)
        completas.setdefault((mercado, linha), []).append((ts, casa_id, odds_por_sel))

    fechamentos: dict[tuple, RevisaoFechamento] = {}
    indisponiveis: list[FechamentoIndisponivel] = []

    for chave in sorted(vistos):
        mercado, linha = chave
        candidatas = completas.get(chave)
        if not candidatas:
            indisponiveis.append(FechamentoIndisponivel(
                mercado=mercado, linha=linha, motivo="sem_revisao_completa",
                limite_s=limite_idade_s))
            continue

        # Mais recente; empate entre casas de referência resolvido por casa_id
        # (determinismo). Com mais de uma referência, a PRIORIDADE é decisão de
        # rito — ver PC-REFERENCIA-MULTIPLA; hoje só a Pinnacle é referência.
        ts, casa_id, odds_por_sel = max(candidatas, key=lambda c: (c[0], c[1]))
        idade_s = (inicio - ts).total_seconds()
        if idade_s > limite_idade_s:
            indisponiveis.append(FechamentoIndisponivel(
                mercado=mercado, linha=linha, motivo="revisao_completa_defasada",
                limite_s=limite_idade_s, idade_s=idade_s))
            continue

        ordem = ORDEM_SELECAO[mercado]
        try:
            probs, _z = devig_shin([odds_por_sel[sel] for sel in ordem])
        except ValueError:
            indisponiveis.append(FechamentoIndisponivel(
                mercado=mercado, linha=linha, motivo="sem_revisao_completa",
                limite_s=limite_idade_s))
            continue
        fechamentos[chave] = RevisaoFechamento(
            casa_id=casa_id, mercado=mercado, linha=linha, ts_fonte=ts,
            idade_s=idade_s, probs=dict(zip(ordem, probs)),
        )
    return fechamentos, indisponiveis


def _linha_clv(
    *, sinal_id: Optional[str], aborto_id: Optional[int], odd_emissao: float,
    p_emissao: float, p_fechamento: float, contrafactual: bool, ts_fechamento: str,
) -> dict[str, Any]:
    return {
        "sinal_id": sinal_id,
        "aborto_l1_id": aborto_id,
        "contrafactual": contrafactual,
        "odd_emissao": odd_emissao,
        "odd_fechamento_ref": round(1.0 / p_fechamento, 4),
        "p_emissao": p_emissao,
        "p_fechamento": round(p_fechamento, 6),
        "clv_pct": round(clv_pct(odd_emissao, p_fechamento), 3),
        "ts_fechamento": ts_fechamento,
    }


# Categoria do desfecho por origem do item (Doutrina §3 / Sugestão nº 10). Nunca se
# somam na mesma média: CLV real, decisório, operacional e de calibração respondem
# perguntas diferentes.
CATEGORIA_POR_STATUS: dict[str, str] = {
    "confirmado": "real",
    "vetado": "contrafactual_l2",
    "expirado": "contrafactual_operacional",
    "erro": "contrafactual_operacional",
    "timeout_crivo": "contrafactual_operacional",
}
STATUS_AUDITAVEIS = tuple(CATEGORIA_POR_STATUS)

MOTIVO_POR_STATUS: dict[str, str] = {
    "expirado": "preco_deixou_de_ser_executavel",
    "erro": "falha_tecnica_no_crivo",
    "timeout_crivo": "crivo_nao_concluido_antes_do_kickoff",
}


@dataclass(frozen=True)
class ItemFechamento:
    """Um item auditável do evento: sinal OU aborto rastreado (inclui candidato_sombra)."""
    sinal_id: Optional[str]
    aborto_id: Optional[int]
    categoria: str
    mercado: str
    selecao: str
    linha: Optional[float]
    odd_emissao: float
    p_emissao: float
    contrafactual: bool
    motivo_origem: Optional[str] = None

    @property
    def ref(self) -> str:
        return f"sinal={self.sinal_id}" if self.sinal_id else f"aborto={self.aborto_id}"


# `_gravar_clv` (INSERT direto em `clv_log`) foi REMOVIDO pela migration 0007: a
# gravação passou a ser a RPC `fn_registrar_resultado_clv`, que grava a medição e o
# desfecho terminal na MESMA transação. Uma medição sem desfecho reabriria o buraco
# do achado #4 (item medido que o evento nunca contabiliza). A absorção da corrida
# continua — agora dentro da própria função SQL, que devolve `registrado=false`.



def _itens_do_evento(banco: Any, evento_id: str) -> list[ItemFechamento]:
    """Todos os itens auditáveis do evento (Sugestão nº 10).

    Sinais em QUALQUER status já resolvido — não só confirmado/vetado: `expirado`,
    `erro` e `timeout_crivo` também merecem desfecho, senão o evento nunca finaliza e
    a amostra some sem registro. Mais os abortos rastreados (near-miss do L1 e
    candidatos_sombra de mercado não homologado, distinguidos pela categoria).
    """
    itens: list[ItemFechamento] = []
    for s in banco.sinais_do_evento(evento_id, status=list(STATUS_AUDITAVEIS)):
        itens.append(ItemFechamento(
            sinal_id=s["id"], aborto_id=None,
            categoria=CATEGORIA_POR_STATUS[s["status"]],
            mercado=s["mercado"], selecao=s["selecao"], linha=_linha_key(s.get("linha")),
            odd_emissao=float(s["odd_venue"]), p_emissao=float(s["p_justa"]),
            contrafactual=(s["status"] != "confirmado"),
            motivo_origem=MOTIVO_POR_STATUS.get(s["status"]),
        ))
    for a in banco.abortos_rastreados_do_evento(evento_id):
        dp = a.get("dossie_parcial") or {}
        sel, odd = dp.get("selecao"), dp.get("odd_venue")
        if sel is None or odd is None or dp.get("mercado") is None:
            # Dossiê parcial sem o mínimo para medir CLV: desfecho terminal próprio,
            # nunca um item eternamente pendente travando o evento.
            itens.append(ItemFechamento(
                sinal_id=None, aborto_id=a["id"], categoria="contrafactual_l1",
                mercado=str(dp.get("mercado") or "?"), selecao=str(sel or "?"),
                linha=_linha_key(dp.get("linha")), odd_emissao=0.0, p_emissao=0.0,
                contrafactual=True, motivo_origem="dossie_parcial_incompleto",
            ))
            continue
        # candidato_sombra (achado 8) é um aborto com este gate — categoria calibração.
        calibracao = a.get("gate_reprovado") == "mercado_nao_homologado"
        itens.append(ItemFechamento(
            sinal_id=None, aborto_id=a["id"],
            categoria="calibracao" if calibracao else "contrafactual_l1",
            mercado=dp["mercado"], selecao=sel, linha=_linha_key(dp.get("linha")),
            odd_emissao=float(odd),
            p_emissao=float(dp.get("p_justa") or prob_implicita(float(odd))),
            contrafactual=True,
        ))
    return itens


def processar_evento(banco: Any, evento: dict[str, Any], gates: Any) -> dict[str, int]:
    """Dá desfecho terminal a cada item do evento e, se todos terminarem, finaliza.

    Substitui `fechar_evento` (achado #4). O evento NÃO é mais marcado 'encerrado'
    por conta própria: quem decide é `fn_finalizar_evento_clv`, que recusa enquanto
    houver item sem desfecho — o app não declara sozinho que acabou.

    FALHA DE INFRAESTRUTURA NÃO É DESFECHO (Doutrina §3): erro de rede/banco deixa o
    item PENDENTE para o próximo ciclo. Gravar 'sem book' por causa de um timeout do
    Supabase seria mentir sobre o dado — e o registro é append-only, indesdizível.
    """
    resumo = {"itens": 0, "calculados": 0, "indisponiveis": 0, "pendentes": 0}
    inicio = para_datetime(evento.get("inicio_utc"))
    if inicio is None:
        return resumo

    itens = _itens_do_evento(banco, evento["id"])
    resumo["itens"] = len(itens)
    if not itens:
        return resumo

    com_desfecho_sinais, com_desfecho_abortos = banco.itens_com_desfecho(evento["id"])
    pendentes = [i for i in itens
                 if (i.sinal_id not in com_desfecho_sinais if i.sinal_id
                     else i.aborto_id not in com_desfecho_abortos)]
    if not pendentes:
        _finalizar_se_completo(banco, evento["id"], resumo)
        return resumo

    ref_ids = [c["id"] for c in banco.casas_ativas() if c.get("tipo") == "referencia"]
    if not ref_ids:
        # Sem referência ativa é INDISPONIBILIDADE nomeada, não silêncio nem retry
        # eterno: o CLV daquele evento não existe e isso fica registrado.
        _log.warning("sem casa de referência ativa — itens ficam sem CLV (P6)",
                     extra={"evento_id": evento["id"]})
        for item in pendentes:
            _registrar(banco, evento["id"], item, "indisponivel_sem_referencia",
                       motivo="nenhuma_casa_referencia_ativa", resumo=resumo)
        _finalizar_se_completo(banco, evento["id"], resumo)
        return resumo

    limite_idade_s = float(gates.get("fechamento_idade_max_s"))
    snaps_ref = banco.snapshots_do_evento(
        evento["id"], casa_ids=ref_ids, ate_iso=evento["inicio_utc"])
    fechamentos, indisponiveis = revisoes_de_fechamento(
        snaps_ref, inicio=inicio, limite_idade_s=limite_idade_s)
    por_mercado = {(i.mercado, i.linha): i for i in indisponiveis}

    for item in pendentes:
        chave = (item.mercado, item.linha)
        rev = fechamentos.get(chave)
        if rev is not None and item.odd_emissao > 0:
            p = rev.probs.get(item.selecao)
            if p is not None:
                _registrar(banco, evento["id"], item, "calculado",
                           motivo=item.motivo_origem, rev=rev, p_fechamento=p,
                           resumo=resumo)
                continue
        # Sem fechamento utilizável: nomeia o porquê (nunca perda silenciosa).
        ind = por_mercado.get(chave)
        if item.odd_emissao <= 0:
            resultado, motivo, rev_ind = ("indisponivel_evento_inconsistente",
                                          item.motivo_origem, None)
        elif ind is not None and ind.motivo == "revisao_completa_defasada":
            resultado, motivo, rev_ind = "indisponivel_revisao_defasada", None, ind
        else:
            resultado, motivo, rev_ind = "indisponivel_sem_revisao_completa", None, ind
        _registrar(banco, evento["id"], item, resultado, motivo=motivo,
                   idade_s=getattr(rev_ind, "idade_s", None), resumo=resumo)

    _finalizar_se_completo(banco, evento["id"], resumo)
    return resumo


def _registrar(banco: Any, evento_id: str, item: ItemFechamento, resultado: str, *,
               motivo: Optional[str] = None, rev: Optional[RevisaoFechamento] = None,
               p_fechamento: Optional[float] = None, idade_s: Optional[float] = None,
               resumo: dict[str, int]) -> None:
    """Grava o desfecho terminal do item (RPC atômica). Falha de infraestrutura deixa
    o item PENDENTE — não vira desfecho."""
    campos: dict[str, Any] = {
        "p_evento_id": evento_id, "p_sinal_id": item.sinal_id, "p_aborto_id": item.aborto_id,
        "p_categoria": item.categoria, "p_resultado": resultado, "p_motivo": motivo,
        "p_mercado": item.mercado, "p_selecao": item.selecao, "p_linha": item.linha,
        "p_idade_s": (rev.idade_s if rev else idade_s),
        "p_ts_fechamento": (rev.ts_fonte.isoformat() if rev else None),
    }
    if resultado == "calculado" and rev is not None and p_fechamento is not None:
        campos.update({
            "p_contrafactual": item.contrafactual,
            "p_odd_emissao": item.odd_emissao,
            "p_odd_fechamento_ref": round(1.0 / p_fechamento, 4),
            "p_p_emissao": item.p_emissao,
            "p_p_fechamento": round(p_fechamento, 6),
            "p_clv_pct": round(clv_pct(item.odd_emissao, p_fechamento), 3),
        })
    try:
        r = banco.registrar_resultado_clv(**campos)
    except Exception:
        # Rede/banco fora: NÃO é desfecho. Fica pendente para o próximo ciclo.
        _log.exception("falha ao registrar desfecho — item segue pendente",
                       extra={"evento_id": evento_id, "ref": item.ref})
        resumo["pendentes"] += 1
        return
    if not r.get("registrado"):
        return                                   # já tinha desfecho (corrida)
    if resultado == "calculado":
        resumo["calculados"] += 1
    else:
        resumo["indisponiveis"] += 1
        _log.warning("item sem CLV — desfecho registrado", extra={
            "evento_id": evento_id, "ref": item.ref, "mercado": item.mercado,
            "resultado": resultado, "motivo": motivo, "idade_s": idade_s,
        })


def _finalizar_se_completo(banco: Any, evento_id: str, resumo: dict[str, int]) -> None:
    """Pede a finalização do evento. O BANCO recusa se ainda houver item pendente —
    a recusa é esperada e não é erro."""
    if resumo["pendentes"]:
        return
    try:
        banco.finalizar_evento_clv(evento_id)
    except Exception as e:
        _log.info("evento ainda não finalizável", extra={"evento_id": evento_id,
                                                         "detalhe": str(e)})


def rodar_fechamento(banco: Any, gates: Any, agora_iso: str, *,
                     limite: int = 200) -> dict[str, int]:
    """Um ciclo do L4. Pulsa heartbeat `l4`."""
    # Nenhum sinal sobrevive ao kickoff em 'aguardando_crivo' (Sugestão nº 10):
    # senão o item nunca teria desfecho e o evento nunca finalizaria.
    timeouts = banco.marcar_timeout_crivo(agora_iso)

    assentamento_s = float(gates.get("fechamento_assentamento_s"))
    eventos = banco.fila_fechamento(agora_iso, assentamento_s, limite)
    total = {"eventos": 0, "clv": 0, "indisponiveis": 0, "pendentes": 0,
             "timeout_crivo": timeouts}
    for evento in eventos:
        r = processar_evento(banco, evento, gates)
        total["eventos"] += 1
        total["clv"] += r["calculados"]
        total["indisponiveis"] += r["indisponiveis"]
        total["pendentes"] += r["pendentes"]
    banco.pulsar(DAEMON, dict(total))
    _log.info("ciclo L4 concluído", extra=dict(total))
    return total
