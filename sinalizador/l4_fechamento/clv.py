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


def _gravar_clv(banco: Any, linha: dict[str, Any], ref: str) -> bool:
    """Grava uma linha de `clv_log`. Devolve False (sem erro) se o CLV já existia.

    A unicidade real é do BANCO (`ux_clv_sinal`/`ux_clv_aborto`, migration 0005): o
    dedup em código é caminho rápido, não garantia — entre a leitura dos ids já
    registrados e este INSERT, outro processo do L4 pode ter gravado o mesmo CLV.
    Nesse caso o banco recusa, e a recusa é o resultado desejado (o CLV existe).

    Engolir SÓ a violação de unicidade é essencial: sem isso a corrida derrubaria o
    fechamento do evento inteiro, deixando SEM CLV os demais sinais — perder amostra
    do KPI soberano por causa de uma duplicata evitada. Qualquer outro erro sobe.
    """
    try:
        banco.inserir("clv_log", linha)
        return True
    except Exception as e:
        if e_violacao_unicidade(e):
            _log.info("CLV já registrado por outro processo (corrida) — ignorado",
                      extra={"ref": ref})
            return False
        raise


def fechar_evento(banco: Any, evento: dict[str, Any], gates: Any) -> int:
    """Fecha o CLV de um evento já iniciado. Devolve quantas linhas de `clv_log`
    gravou. Marca o evento 'encerrado' ao fim (sai da fila do L4)."""
    inicio_iso = evento.get("inicio_utc")
    inicio = para_datetime(inicio_iso)
    if inicio is None:
        return 0
    ref_ids = [c["id"] for c in banco.casas_ativas() if c.get("tipo") == "referencia"]
    if not ref_ids:
        _log.warning("sem casa de referência ativa — fechamento impossível (P6)")
        return 0
    limite_idade_s = float(gates.get("fechamento_idade_max_s"))
    snaps_ref = banco.snapshots_do_evento(evento["id"], casa_ids=ref_ids, ate_iso=inicio_iso)
    fechamentos, indisponiveis = revisoes_de_fechamento(
        snaps_ref, inicio=inicio, limite_idade_s=limite_idade_s)

    # Perda de amostra NUNCA é silenciosa (Sugestão nº 9): registra mercado, motivo,
    # idade e limite — é o que responde depois "quantos CLVs deixaram de ser
    # calculados, em quais mercados, e se o gate está cortando amostra demais".
    for ind in indisponiveis:
        _log.warning("sem linha de fechamento — CLV não calculado", extra={
            "evento_id": evento["id"], "mercado": ind.mercado, "linha": ind.linha,
            "motivo": ind.motivo, "idade_s": ind.idade_s, "limite_s": ind.limite_s,
        })
    if not fechamentos:
        return 0

    # Achado #2.1: pergunta ao banco só pelos ids DESTE evento (`in_`), em vez de
    # varrer `clv_log` inteira. Por isso sinais e abortos são carregados ANTES — é
    # deles que saem os ids da consulta. Caminho rápido; a garantia é o índice único.
    sinais = banco.sinais_do_evento(evento["id"], status=["confirmado", "vetado"])
    abortos = banco.abortos_rastreados_do_evento(evento["id"])
    sinal_ids_com_clv, aborto_ids_com_clv = banco.clv_ids_registrados(
        sinal_ids=[s["id"] for s in sinais],
        aborto_ids=[a["id"] for a in abortos],
    )
    gravadas = 0

    for sinal in sinais:
        if sinal["id"] in sinal_ids_com_clv:
            continue
        rev = fechamentos.get((sinal["mercado"], _linha_key(sinal.get("linha"))))
        p = rev.probs.get(sinal["selecao"]) if rev else None
        if p is None:
            continue
        # ts_fechamento = carimbo REAL da revisão usada (Sugestão nº 9), não o
        # kickoff: o início segue em `eventos.inicio_utc` e a defasagem sai do join.
        if _gravar_clv(banco, _linha_clv(
            sinal_id=sinal["id"], aborto_id=None, odd_emissao=float(sinal["odd_venue"]),
            p_emissao=float(sinal["p_justa"]), p_fechamento=p,
            contrafactual=(sinal["status"] == "vetado"),
            ts_fechamento=rev.ts_fonte.isoformat(),
        ), ref=f"sinal={sinal['id']}"):
            gravadas += 1

    for aborto in abortos:
        if aborto["id"] in aborto_ids_com_clv:
            continue
        dp = aborto.get("dossie_parcial") or {}
        sel, odd_venue = dp.get("selecao"), dp.get("odd_venue")
        if sel is None or odd_venue is None:
            continue
        rev = fechamentos.get((dp.get("mercado"), _linha_key(dp.get("linha"))))
        p = rev.probs.get(sel) if rev else None
        if p is None:
            continue
        if _gravar_clv(banco, _linha_clv(
            sinal_id=None, aborto_id=aborto["id"], odd_emissao=float(odd_venue),
            p_emissao=float(dp.get("p_justa") or prob_implicita(odd_venue)),
            p_fechamento=p, contrafactual=True,
            ts_fechamento=rev.ts_fonte.isoformat(),
        ), ref=f"aborto={aborto['id']}"):
            gravadas += 1

    banco.marcar_evento_encerrado(evento["id"])
    _log.info("evento fechado", extra={"evento_id": evento["id"], "clv_gravadas": gravadas})
    return gravadas


def rodar_fechamento(banco: Any, gates: Any, agora_iso: str, *, limite: int = 200) -> dict[str, int]:
    """Fecha todos os eventos já iniciados e ainda abertos. Pulsa heartbeat `l4`."""
    eventos = banco.eventos_iniciados_sem_status_final(agora_iso, limite)
    total_eventos = 0
    total_clv = 0
    for evento in eventos:
        n = fechar_evento(banco, evento, gates)
        total_eventos += 1
        total_clv += n
    banco.pulsar(DAEMON, {"eventos_fechados": total_eventos, "clv_gravadas": total_clv})
    _log.info("ciclo L4 concluído", extra={"eventos": total_eventos, "clv": total_clv})
    return {"eventos": total_eventos, "clv": total_clv}
