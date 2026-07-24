"""Wiring L0→L1 — snapshots reais alimentando os gatilhos reais.

Lê `odds_snapshots` (capturados pelo L0), reconstrói por (evento, mercado, linha)
a referência (Pinnacle de-vigada por Shin) e os venues, e roda o pipeline
mecânico do L1 sobre cada seleção:

  referência (devig Shin) → edge líquido (comissão da tabela `casas`)
    → gatilhos (value_bet / odds_drop / anomalia — E2.4) → motor de gates
      → dossiê completo + fila do L2  (sinal)   OU   `abortos_l1` (near-miss).

SEM IA (regra 2), SEM dinheiro (P1). "Dado ausente = abortar" (P6): book de
referência incompleto, sem venue capturado, sem banca ou sem carimbo de fonte →
não gera candidato (registra e segue), nunca chuta.

Política de venue (`PoliticaVenue`):
  - `EXCHANGE` (doutrina-puro): venue = casa `exchange` (Betfair). O gate de
    liquidez se aplica. Sem exchange com book capturado (E1.2 suspenso), não há
    sinal — o exchange-proxy sem book aborta no gate; só o log de abortos e o
    rastreio de CLV alimentam a calibração desde já.
  - `RETAIL_SOMBRA` (RATIFICADO pela Sugestão nº 6 para o modo sombra): venue =
    melhor preço de VAREJO. O gate de liquidez é inaplicável (varejo não tem book);
    em odd fixa `slippage=0` é DEFINIÇÃO, não otimismo (o preço exibido é o
    executável e a `odd_minima_aceitavel` protege contra movimento — Doutrina
    §-sombra). O sinal sai marcado `sombra_varejo=True` (honestidade preservada).
    Dinheiro real segue travado pelo gate do E7 — o modo sombra só mede CLV, que
    não exige book. O exchange-proxy `betfair_ex_*` fica FORA do venue sombra até
    o rito ratificar seu tratamento sem-book (PC-EXCHANGE-PROXY).
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional

from sinalizador.comum.modelos import Dossie

from .abortos import deve_rastrear_clv, registrar_aborto
from .devig import devig_shin
from .dossie import construir_dossie, enfileirar_sinal
from .edge import comissao_fracao, edge_liquido, odd_minima_aceitavel
from .gatilhos import detectar_anomalia, detectar_odds_drop, melhor_preco, variacao_pct
from .motor_gates import (
    ContextoAvaliacao,
    avaliar,
    avaliar_exposicao,
    stake_kelly_fracao,
    tetos_exposicao,
)

_log = logging.getLogger(__name__)

DAEMON = "l1"

# Ordem canônica das seleções por mercado (para o vetor de-vigado do Shin) e
# conjunto obrigatório: book de referência sem TODAS as seleções → sem devig (P6).
ORDEM_SELECAO: dict[str, tuple[str, ...]] = {
    "1x2": ("1", "X", "2"),
    "ou": ("over", "under"),
    "ah": ("mandante", "visitante"),
}


class PoliticaVenue(str, Enum):
    EXCHANGE = "exchange"
    RETAIL_SOMBRA = "retail_sombra"


PontoSerie = tuple[datetime, float]


@dataclass
class ResumoL1:
    grupos: int = 0
    sinais: int = 0
    abortos: int = 0
    rastreados_clv: int = 0
    pulados: list[str] = field(default_factory=list)  # motivos de skip (P6)


def _dt(valor: Any) -> Optional[datetime]:
    """ISO 8601 → datetime aware (UTC). None se ausente/inválido — nunca chuta."""
    if isinstance(valor, datetime):
        return valor if valor.tzinfo else valor.replace(tzinfo=timezone.utc)
    if not valor:
        return None
    try:
        dt = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _ultimo(serie: list[PontoSerie]) -> Optional[PontoSerie]:
    return max(serie, key=lambda p: p[0]) if serie else None


@dataclass(frozen=True)
class GrupoMercado:
    """Tudo de um (evento, mercado, linha) já agrupado e datado."""
    evento_id: str
    evento: dict[str, Any]                                   # liga, partida, data_hora_utc
    mercado: str
    linha: Optional[float]
    ref: dict[str, list[PontoSerie]]                         # selecao → série (ts, odd)
    venue: dict[str, dict[str, list[tuple[datetime, float, Optional[float]]]]]  # sel → casa_id → série


def _casas_venue_da_politica(casas: dict[str, dict], politica: PoliticaVenue) -> set[str]:
    # RETAIL_SOMBRA usa SÓ varejo. O exchange-proxy (betfair_ex_*) já é capturado
    # (Sugestão nº 6, executável) mas fica FORA do venue do modo sombra até o rito
    # ratificar seu tratamento sem-book "com o relatório na mão" — enquanto isso
    # ele alimenta CLV/relatório sem alterar os sinais sombra. EXCHANGE (doutrina-
    # puro) usa a exchange e o gate de liquidez decide (proxy sem book = aborto).
    tipos = {"exchange"} if politica is PoliticaVenue.EXCHANGE else {"varejo"}
    return {cid for cid, c in casas.items() if c.get("tipo") in tipos}


def avaliar_grupo(
    banco: Any,
    grupo: GrupoMercado,
    casas: dict[str, dict],
    gates: Any,
    *,
    banca: float,
    banca_origem: str,
    exposto: dict[str, float],
    agora: datetime,
    politica: PoliticaVenue,
    resumo: ResumoL1,
) -> None:
    """Roda o pipeline para cada seleção do grupo. Escreve sinais/abortos no banco."""
    ordem = ORDEM_SELECAO.get(grupo.mercado)
    if ordem is None:
        resumo.pulados.append(f"{grupo.evento_id}/{grupo.mercado}: mercado fora do escopo")
        return

    # Referência: última odd de cada seleção. Falta alguma → sem devig (P6).
    ref_atual: dict[str, PontoSerie] = {}
    for sel in ordem:
        ult = _ultimo(grupo.ref.get(sel, []))
        if ult is None or ult[1] <= 1.0:
            resumo.pulados.append(f"{grupo.evento_id}/{grupo.mercado}: referência incompleta ({sel})")
            return
        ref_atual[sel] = ult
    try:
        probs, _z = devig_shin([ref_atual[sel][1] for sel in ordem])
    except ValueError as e:
        resumo.pulados.append(f"{grupo.evento_id}/{grupo.mercado}: devig falhou ({e})")
        return
    p_por_sel = dict(zip(ordem, probs))

    casas_venue = _casas_venue_da_politica(casas, politica)
    janela_sinc = float(gates.get("janela_sincronia_s"))
    janela_drop = float(gates.get("janela_drop_s"))
    anomalia_lim = float(gates.get("anomalia_move_pct"))
    edge_min_frac = float(gates.get("edge_min_pct")) / 100.0

    for sel in ordem:
        p_justa = p_por_sel[sel]
        ts_ref, odd_ref = ref_atual[sel]

        # Venues capturados para esta seleção (line shopping): última odd por casa.
        candidatos_venue: list[dict[str, Any]] = []
        for casa_id, serie in grupo.venue.get(sel, {}).items():
            if casa_id not in casas_venue or not serie:
                continue
            ts_v, odd_v, liq_v = max(serie, key=lambda p: p[0])
            candidatos_venue.append(
                {"casa_id": casa_id, "casa": casas[casa_id]["nome"], "tipo": casas[casa_id].get("tipo"),
                 "odd": odd_v, "ts_fonte": ts_v, "liquidez": liq_v}
            )
        melhor = melhor_preco(candidatos_venue)
        if melhor is None:
            resumo.pulados.append(f"{grupo.evento_id}/{grupo.mercado}/{sel}: sem venue capturado")
            continue

        casa_row = casas[melhor["casa_id"]]
        comissao = comissao_fracao(casa_row)
        # slippage 0: em varejo de odd fixa é DEFINIÇÃO (Doutrina §-sombra / Sugestão
        # nº 6) — o preço exibido é o executável. Estimador só p/ venue de exchange.
        edge = edge_liquido(p_justa, melhor["odd"], comissao)

        # Gatilhos (sobre a série da referência e do venue escolhido).
        serie_ref = grupo.ref.get(sel, [])
        serie_venue = [(t, o) for (t, o, _l) in grupo.venue.get(sel, {}).get(melhor["casa_id"], [])]
        drop_disparou, _queda = detectar_odds_drop(serie_ref, gates, agora)
        move_ref = variacao_pct(serie_ref, janela_drop, agora)
        move_venue = variacao_pct(serie_venue, janela_drop, agora)
        anomalo = detectar_anomalia(move_ref, move_venue, gates)
        gatilho = "odds_drop" if drop_disparou else "value_bet"
        caminho = "profundo" if anomalo else "rapido"
        # Estabilidade da referência: derivada (referência parada = não se moveu
        # ≥ anomalia_move_pct na janela). Reutiliza o gate, não cria um novo.
        referencia_estavel = abs(move_ref) < anomalia_lim

        stake_frac = stake_kelly_fracao(p_justa, melhor["odd"], gates)
        stake_valor = stake_frac * banca

        eh_exchange = casa_row.get("tipo") == "exchange"
        aplica_liquidez = eh_exchange  # varejo não tem book (P6: não se inventa liquidez)
        liquidez_disp = float(melhor["liquidez"]) if melhor["liquidez"] is not None else 0.0

        ctx = ContextoAvaliacao(
            odd_venue=melhor["odd"],
            edge_liquido=edge,
            stake_valor=stake_valor,
            liquidez_disponivel=liquidez_disp,
            ts_fonte_referencia=ts_ref,
            ts_fonte_venue=melhor["ts_fonte"],
            referencia_estavel_ok=referencia_estavel,
            agora=agora,
        )
        veredito = avaliar(ctx, gates, avaliar_liquidez=aplica_liquidez)

        dossie_parcial = {
            "evento_id": grupo.evento_id, "mercado": grupo.mercado, "selecao": sel,
            "linha": grupo.linha, "p_justa": p_justa, "odd_referencia": odd_ref,
            "odd_venue": melhor["odd"], "casa_venue": melhor["casa"],
            "edge_liquido": edge, "comissao": comissao, "gatilho": gatilho,
        }

        if not veredito.aprovado:
            _registrar(banco, grupo, sel, veredito.gate_reprovado, dossie_parcial,
                       edge, gates, resumo)
            continue

        # Gate de exposição em camadas (agregado).
        tetos = tetos_exposicao(gates, banca)
        vexp = avaliar_exposicao(stake_valor, exposto, tetos)
        if not vexp.aprovado:
            _registrar(banco, grupo, sel, vexp.gate_reprovado, dossie_parcial,
                       edge, gates, resumo)
            continue

        # Aprovado → dossiê completo + fila do L2.
        odd_min = odd_minima_aceitavel(p_justa, comissao, edge_min_frac)
        dossie = _montar_dossie(
            grupo=grupo, sel=sel, gatilho=gatilho, gatilho_anomalo=anomalo, caminho=caminho,
            p_justa=p_justa, odd_ref=odd_ref, melhor=melhor, edge=edge, comissao=comissao,
            stake_frac=stake_frac, odd_min=odd_min, ts_ref=ts_ref, janela_sinc=janela_sinc,
            referencia_estavel=referencia_estavel, serie_ref=serie_ref, serie_venue=serie_venue,
            candidatos_venue=candidatos_venue, exposto=exposto, liquidez_disp=liquidez_disp,
            aplica_liquidez=aplica_liquidez, politica=politica, banca_origem=banca_origem,
        )
        enfileirar_sinal(banco, dossie, evento_id=grupo.evento_id,
                         casa_venue_id=melhor["casa_id"], linha=grupo.linha)
        resumo.sinais += 1
        _log.info("sinal enfileirado", extra={"evento": grupo.evento_id, "mercado": grupo.mercado,
                                               "selecao": sel, "gatilho": gatilho, "caminho": caminho,
                                               "edge_pct": round(edge * 100, 2)})


def _registrar(banco, grupo, sel, gate_reprovado, dossie_parcial, edge, gates, resumo: ResumoL1) -> None:
    rastrear = gate_reprovado == "edge_min_pct" and deve_rastrear_clv(edge, gates)
    registrar_aborto(banco, gatilho=dossie_parcial["gatilho"], gate_reprovado=gate_reprovado or "desconhecido",
                     dossie_parcial=dossie_parcial, evento_id=grupo.evento_id, clv_rastrear=rastrear)
    resumo.abortos += 1
    if rastrear:
        resumo.rastreados_clv += 1


def _serie_1h(serie: list[PontoSerie], agora: datetime) -> list[dict[str, Any]]:
    corte = agora - timedelta(hours=1)
    pts = sorted([(t, o) for (t, o) in serie if t >= corte], key=lambda p: p[0])
    return [{"ts": t.isoformat(), "odd": o} for t, o in pts[-30:]]  # ≤30 (Sugestão nº 2)


def _montar_dossie(
    *, grupo, sel, gatilho, gatilho_anomalo, caminho, p_justa, odd_ref, melhor, edge,
    comissao, stake_frac, odd_min, ts_ref, janela_sinc, referencia_estavel, serie_ref,
    serie_venue, candidatos_venue, exposto, liquidez_disp, aplica_liquidez, politica,
    banca_origem,
) -> Dossie:
    sincronia_ok = abs((melhor["ts_fonte"] - ts_ref).total_seconds()) <= janela_sinc
    liquidez: dict[str, Any] = {
        "disponivel_no_preco": liquidez_disp,
        "profundidade_book": None,
        # Sugestão nº 8: distingue "inaplicável" de "reprovado". No varejo sombra a
        # liquidez é inaplicável (Doutrina §3) → gate_liquidez_ok=None (não avaliado),
        # jamais False (que o V-A5 leria como reprovação e vetaria todo sinal sombra).
        "liquidez_aplicavel": bool(aplica_liquidez),
        "gate_liquidez_ok": True if aplica_liquidez else None,
    }
    if politica is PoliticaVenue.RETAIL_SOMBRA and not aplica_liquidez:
        liquidez["sombra_varejo"] = True  # extra="allow": marca o desvio no dossiê
    dados = {
        "sinal_id": str(uuid.uuid4()),
        "gatilho": gatilho,
        "gatilho_anomalo": gatilho_anomalo,
        "caminho": caminho,
        # Sugestão nº 7: origem da banca do sizing ('real' | 'papel'). extra="allow".
        "banca_origem": banca_origem,
        "evento": {
            "liga": grupo.evento.get("liga", ""),
            "partida": grupo.evento.get("partida", ""),
            "data_hora_utc": grupo.evento.get("data_hora_utc"),
            "mercado": grupo.mercado,
            "selecao": sel,
        },
        "matematica": {
            "p_justa_shin": p_justa,
            "odd_referencia": odd_ref,
            "odd_venue": melhor["odd"],
            "edge_liquido": edge,
            "stake_kelly_quarto": stake_frac,
            "odd_minima_aceitavel": odd_min,
            "comissao_aplicada": comissao,
        },
        "snapshots": {
            "ts_fonte_referencia": ts_ref.isoformat(),
            "ts_fonte_venue": melhor["ts_fonte"].isoformat(),
            "janela_sincronia_ok": sincronia_ok,
            "referencia_estavel_ok": referencia_estavel,
            "historico_movimento_1h": {
                "referencia": _serie_1h(serie_ref, melhor["ts_fonte"]),
                "venue": _serie_1h(serie_venue, melhor["ts_fonte"]),
            },
        },
        "liquidez": liquidez,
        "venues_comparados": [
            {"casa": v["casa"], "odd": v["odd"], "ts_fonte": v["ts_fonte"].isoformat()}
            for v in candidatos_venue
        ],
        "exposicao": {
            "por_jogo": exposto.get("jogo", 0.0),
            "por_liga_dia": exposto.get("liga_dia", 0.0),
            "por_dia": exposto.get("dia", 0.0),
            "gates_exposicao_ok": True,
        },
        "tipster": None,
    }
    return construir_dossie(dados)


# ------------------------- carregamento (banco → grupos) -------------------------


def _linha_key(linha: Any) -> Optional[float]:
    if linha is None:
        return None
    return round(float(linha), 2)


def agrupar_snapshots(
    snaps: list[dict[str, Any]], casas: dict[str, dict], eventos: dict[str, dict]
) -> list[GrupoMercado]:
    """Constrói os GrupoMercado a partir das linhas cruas de `odds_snapshots`."""
    tmp: dict[tuple, dict] = {}
    for s in snaps:
        ts = _dt(s.get("ts_fonte"))
        casa = casas.get(s.get("casa_id"))
        if ts is None or casa is None or s.get("odd") is None:
            continue
        chave = (s["evento_id"], s["mercado"], _linha_key(s.get("linha")))
        g = tmp.setdefault(chave, {"ref": {}, "venue": {}})
        sel = s["selecao"]
        odd = float(s["odd"])
        if casa.get("tipo") == "referencia":
            g["ref"].setdefault(sel, []).append((ts, odd))
        else:
            liq = s.get("liquidez")
            g["venue"].setdefault(sel, {}).setdefault(s["casa_id"], []).append(
                (ts, odd, float(liq) if liq is not None else None)
            )

    grupos: list[GrupoMercado] = []
    for (evento_id, mercado, linha), g in tmp.items():
        ev = eventos.get(evento_id, {})
        partida = f"{ev.get('mandante', '?')} x {ev.get('visitante', '?')}"
        grupos.append(GrupoMercado(
            evento_id=evento_id,
            evento={"liga": ev.get("liga", ""), "partida": partida,
                    "data_hora_utc": ev.get("inicio_utc")},
            mercado=mercado, linha=linha, ref=g["ref"], venue=g["venue"],
        ))
    return grupos


def _exposto_do_evento(exposicao_aberta: list[dict], evento_id: str, liga: str, dia: str) -> dict[str, float]:
    """Extrai {jogo, liga_dia, dia} das linhas de vw_exposicao_aberta (grouping sets)."""
    out = {"jogo": 0.0, "liga_dia": 0.0, "dia": 0.0}
    for r in exposicao_aberta:
        exp = float(r.get("exposto") or 0.0)
        if r.get("evento_id") == evento_id and r.get("liga") == liga:
            out["jogo"] = exp
        elif r.get("evento_id") is None and r.get("liga") == liga and str(r.get("dia")) == dia:
            out["liga_dia"] = exp
        elif r.get("evento_id") is None and r.get("liga") is None and str(r.get("dia")) == dia:
            out["dia"] = exp
    return out


def _banca_papel(banco: Any) -> Optional[float]:
    """Valor nominal da banca de papel (`config_sistema.banca_papel`), ou None se
    ausente/não-numérica. Usada SÓ com o ledger real vazio (Sugestão nº 7) — o
    ledger real nunca é tocado por ela."""
    ler = getattr(banco, "config_vigente", None)
    if ler is None:
        return None
    doc = ler("banca_papel")
    if not doc or not doc.get("valor"):
        return None
    try:
        return float(str(doc["valor"]).strip())
    except (TypeError, ValueError):
        _log.warning("banca_papel na config_sistema não é número — ignorada",
                     extra={"valor": doc.get("valor")})
        return None


def rodar_l1(
    banco: Any,
    gates: Any,
    *,
    agora: datetime,
    politica: PoliticaVenue = PoliticaVenue.EXCHANGE,
    lookback_s: float = 3600.0,
) -> ResumoL1:
    """Um ciclo do L1: carrega snapshots da janela, roda o pipeline, pulsa o heartbeat.

    `agora` é injetado (nunca chuta relógio no core). Sem banca real nem de papel
    → não há sizing → nenhum sinal (P5/P6): registra e sai.
    """
    resumo = ResumoL1()
    desde = (agora - timedelta(seconds=lookback_s)).isoformat()
    snaps = banco.snapshots_desde(desde)
    casas = {c["id"]: c for c in banco.casas_ativas()}
    evento_ids = sorted({s["evento_id"] for s in snaps})
    eventos = {e["id"]: e for e in banco.eventos_por_ids(evento_ids)}

    banca_row = banco.banca_atual()
    # P9 (kill switch) — achado 4 da auditoria: drawdown ≥ suspensão SUSPENDE a
    # EMISSÃO de sinais. A trava é aqui, DURA, antes de dimensionar qualquer coisa:
    # o alerta do L3 chega depois: se o L1 não parar aqui, ele já teria enfileirado
    # sinais novos. A captura (L0) e o CLV (L4) seguem — só a emissão para (Doutrina §P9).
    if banca_row and banca_row.get("kill_switch"):
        _log.warning("kill switch ativo (drawdown ≥ suspensão) — L1 não emite sinais (P9)")
        banco.pulsar(DAEMON, {"grupos": 0, "sinais": 0, "abortos": 0, "motivo": "kill_switch"})
        return resumo
    banca = float(banca_row["saldo"]) if banca_row and banca_row.get("saldo") is not None else None
    banca_origem = "real"
    if not banca or banca <= 0:
        # Ledger real vazio → banca de PAPEL (Sugestão nº 7): o modo sombra precisa
        # dimensionar stakes, mas o ledger real fica INTOCADO até o gate do E7. O
        # dossiê nasce marcado banca_origem=papel (honestidade estatística).
        banca = _banca_papel(banco)
        banca_origem = "papel"
        if not banca or banca <= 0:
            _log.warning("sem banca real nem banca de papel — L1 não dimensiona (P5/P6)")
            banco.pulsar(DAEMON, {"grupos": 0, "sinais": 0, "abortos": 0, "motivo": "sem_banca"})
            return resumo
        _log.info("banca de papel em uso (ledger real vazio) — modo sombra",
                  extra={"banca_papel": banca})

    exposicao_aberta = banco.exposicao_aberta()
    grupos = agrupar_snapshots(snaps, casas, eventos)
    for grupo in grupos:
        resumo.grupos += 1
        ev = eventos.get(grupo.evento_id, {})
        dia = str((_dt(ev.get("inicio_utc")) or agora).date())
        exposto = _exposto_do_evento(exposicao_aberta, grupo.evento_id, ev.get("liga", ""), dia)
        avaliar_grupo(banco, grupo, casas, gates, banca=banca, banca_origem=banca_origem,
                      exposto=exposto, agora=agora, politica=politica, resumo=resumo)

    banco.pulsar(DAEMON, {"grupos": resumo.grupos, "sinais": resumo.sinais,
                          "abortos": resumo.abortos, "rastreados_clv": resumo.rastreados_clv,
                          "politica_venue": politica.value, "banca_origem": banca_origem})
    _log.info("ciclo L1 concluído", extra={"grupos": resumo.grupos, "sinais": resumo.sinais,
                                           "abortos": resumo.abortos})
    return resumo
