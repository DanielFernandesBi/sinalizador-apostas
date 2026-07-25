"""Wiring L0→L1 — snapshots reais alimentando os gatilhos reais.

Lê `odds_snapshots` (capturados pelo L0), reconstrói por (evento, mercado, linha)
a referência (Pinnacle de-vigada por Shin) e os venues, e roda o pipeline
mecânico do L1 sobre cada seleção:

  referência (devig Shin) → edge líquido (comissão da tabela `casas`)
    → gatilhos (value_bet / odds_drop / anomalia — E2.4) → motor de gates
      → gate de homologação (Doutrina P2 / achado 8)
        → dossiê completo + fila do L2  (sinal, só mercado HOMOLOGADO)
        OU  `candidato_sombra` (mercado em backtest: só rastreio de CLV, nunca cartão)
        OU  `abortos_l1` (near-miss, ou mercado suspenso/sem homologação configurada).

Homologação de mercado (P2): "só opera em mercados homologados". O L1 consulta
`mercados_homologados` por (liga, mercado). 'homologado' → sinal normal (L2/L3);
'backtest' (calibração) → candidato_sombra, acompanhado até o fechamento SÓ para
medir CLV (alimenta E6.4), jamais confirmado/cartão; 'suspenso'/'caducado' ou SEM
linha (falha de configuração — a ausência NÃO é licença implícita para calibrar) →
grupo pulado, marcador no log de abortos. Fail-closed: sem homologação, sem sinal.

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

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional

from sinalizador.comum.erros import e_violacao_unicidade
from sinalizador.comum.modelos import Dossie

from .abortos import deve_rastrear_clv, registrar_aborto
from .dossie import construir_dossie, enfileirar_sinal
from .edge import comissao_fracao, edge_liquido, odd_minima_aceitavel
from .gatilhos import detectar_anomalia, detectar_odds_drop, melhor_preco, variacao_pct
from .revisao import ORDEM_SELECAO, linha_key, ultima_revisao_completa
from .motor_gates import (
    ContextoAvaliacao,
    avaliar,
    avaliar_exposicao,
    stake_kelly_fracao,
    tetos_exposicao,
)

_log = logging.getLogger(__name__)

DAEMON = "l1"

# ORDEM_SELECAO e a montagem de revisão vivem em `revisao.py` — definição ÚNICA,
# compartilhada com o L4. Reexportada aqui por compatibilidade de import.
__all__ = ["ORDEM_SELECAO", "PoliticaVenue", "chave_candidato", "rodar_l1",
           "agrupar_snapshots", "avaliar_grupo", "ResumoL1", "GrupoMercado"]


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
    candidatos_sombra: int = 0   # achado 8: passou tudo, mercado não homologado (só CLV)
    pos_kickoff: int = 0         # P0.1: grupos recusados por partida já iniciada
    nao_autorizados: int = 0     # achado 8: grupo pulado (suspenso/caducado ou sem config)
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
    # Linhas CRUAS da referência deste grupo: é delas que sai a revisão indivisível
    # (P0.3). As séries acima seguem servindo aos gatilhos (movimento no tempo).
    snaps_ref: list[dict[str, Any]] = field(default_factory=list)
    inicio_utc: Optional[datetime] = None                    # kickoff (trava do P0.1)


def _casas_venue_da_politica(casas: dict[str, dict], politica: PoliticaVenue) -> set[str]:
    # RETAIL_SOMBRA usa SÓ varejo. O exchange-proxy (betfair_ex_*) já é capturado
    # (Sugestão nº 6, executável) mas fica FORA do venue do modo sombra até o rito
    # ratificar seu tratamento sem-book "com o relatório na mão" — enquanto isso
    # ele alimenta CLV/relatório sem alterar os sinais sombra. EXCHANGE (doutrina-
    # puro) usa a exchange e o gate de liquidez decide (proxy sem book = aborto).
    tipos = {"exchange"} if politica is PoliticaVenue.EXCHANGE else {"varejo"}
    return {cid for cid, c in casas.items() if c.get("tipo") in tipos}


def _casa_executavel(
    chave: str, politica: PoliticaVenue, venues_executaveis: Optional[set[str]]
) -> bool:
    """A casa pode ser o VENUE do cartão (achado 6 da auditoria)?

    O consenso (line shopping / V-C2) usa TODAS as casas capturadas, mas o venue do
    SINAL só pode ser uma casa onde o Daniel de fato executa. No modo sombra isso é
    a allowlist confirmada por ele (casas .bet.br) — Doutrina §3 fala em varejo
    EXECUTÁVEL no ambiente brasileiro, não em qualquer casa europeia da coleta. Sem
    allowlist configurada, NENHUMA casa é executável (fail-closed: não se sinaliza o
    que não se pode apostar — P6/utilidade prática). Sob EXCHANGE (doutrina-puro) o
    venue é a exchange; a executabilidade dela é o gate do E1.2/E7."""
    if politica is PoliticaVenue.EXCHANGE:
        return True
    return venues_executaveis is not None and chave in venues_executaveis


def chave_candidato(
    evento_id: str, mercado: str, linha: Optional[float], selecao: str, casa_venue_id: str
) -> str:
    """Chave determinística do candidato (achado 7 da auditoria): a identidade da
    APOSTA — evento|mercado|linha|seleção|casa. Base da unicidade no banco (um sinal
    ABERTO por candidato, índice `ux_sinais_candidato_aberto`) e do dedup de abortos.
    Sem ela, o L1 relê a janela de 1h a cada ciclo e reemitiria o mesmo sinal/aborto a
    cada minuto. `linha` já vem normalizada do grupo (arredondada ou None → '')."""
    ln = "" if linha is None else str(linha)
    return f"{evento_id}|{mercado}|{ln}|{selecao}|{casa_venue_id}"


def _e_duplicidade(exc: Exception) -> bool:
    """Violação do índice de unicidade do candidato (corrida entre daemons): o banco
    rejeita o 2º sinal aberto do mesmo candidato. Backstop do dedup de aplicação — é
    ignorado (o sinal já existe), não é erro real. Reconhecimento compartilhado com o
    L4 (`comum.erros.e_violacao_unicidade`) — a mesma corrida, tabelas diferentes."""
    return e_violacao_unicidade(exc)


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
    venues_executaveis: Optional[set[str]],
    homolog: dict[tuple[str, str], str],
    chaves_abertas: set[str],
    chaves_abortos: set[str],
    resumo: ResumoL1,
) -> None:
    """Roda o pipeline para cada seleção do grupo. Escreve sinais/abortos no banco."""
    ordem = ORDEM_SELECAO.get(grupo.mercado)
    if ordem is None:
        resumo.pulados.append(f"{grupo.evento_id}/{grupo.mercado}: mercado fora do escopo")
        return

    # P0.1 — NADA é criado depois do apito. O L1 lê a janela de lookback e uma
    # revisão pré-jogo continua "fresca" pelo gate de idade por até 600 s: sem esta
    # trava, minutos APÓS o início ainda nasciam sinal, aborto e candidato_sombra.
    # Pior: o L4 pode já ter finalizado o evento (`clv_eventos_finalizados`), e item
    # criado depois disso não reabre a finalização — ficaria sem CLV para sempre.
    # A trava de aplicação é a primeira barreira; a segunda é do banco, na RPC.
    if grupo.inicio_utc is None or grupo.inicio_utc <= agora:
        resumo.pulados.append(
            f"{grupo.evento_id}/{grupo.mercado}: partida já iniciada (ou sem início) — nada é criado")
        resumo.pos_kickoff += 1
        return

    # Gate de homologação de mercado (Doutrina P2 / achado 8) — decisão de GRUPO
    # (liga, mercado). Só 'homologado' segue o caminho normal (L2/L3); 'backtest' (a
    # fase de calibração) vira `candidato_sombra` mais abaixo — POR SELEÇÃO, e só após
    # passar TODOS os gates mecânicos. Mercado 'suspenso'/'caducado' (retirada
    # explícita) ou SEM linha em `mercados_homologados` (FALHA DE CONFIGURAÇÃO — a
    # ausência não é licença implícita para calibrar) não roda gate nem gera candidato:
    # pula o grupo com um marcador fail-loud no log de abortos (P7).
    liga = grupo.evento.get("liga", "")
    status_homolog = homolog.get((liga, grupo.mercado))
    if status_homolog not in ("homologado", "backtest", "calibracao"):
        _marcar_grupo_nao_autorizado(banco, grupo, status_homolog, resumo,
                                     chaves_abortos=chaves_abortos)
        return
    modo_sombra_homolog = status_homolog != "homologado"  # backtest/calibração → candidato_sombra

    # Referência: a última REVISÃO COMPLETA (P0.3). Antes pegava-se o último preço de
    # CADA seleção independentemente, o que monta um book que nunca existiu sempre que
    # a revisão mais recente omite uma seleção. Aqui é pior que no fechamento: este
    # book vira a `p_justa`, logo o edge, logo a existência do sinal.
    rev = ultima_revisao_completa(grupo.snaps_ref, mercado=grupo.mercado,
                                  linha=grupo.linha, ate=agora)
    if rev is None:
        resumo.pulados.append(
            f"{grupo.evento_id}/{grupo.mercado}: sem revisão completa da referência")
        return
    if any(rev.odds[sel] <= 1.0 for sel in ordem):
        resumo.pulados.append(f"{grupo.evento_id}/{grupo.mercado}: odd de referência inválida")
        return
    try:
        p_por_sel = rev.probs()
    except ValueError as e:
        resumo.pulados.append(f"{grupo.evento_id}/{grupo.mercado}: devig falhou ({e})")
        return
    ts_revisao = rev.ts_fonte

    casas_venue = _casas_venue_da_politica(casas, politica)
    janela_sinc = float(gates.get("janela_sincronia_s"))
    janela_drop = float(gates.get("janela_drop_s"))
    anomalia_lim = float(gates.get("anomalia_move_pct"))
    edge_min_frac = float(gates.get("edge_min_pct")) / 100.0

    for sel in ordem:
        p_justa = p_por_sel[sel]
        # Carimbo e odd vêm da MESMA revisão — não de pontos avulsos por seleção.
        ts_ref, odd_ref = ts_revisao, rev.odds[sel]

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
        # Consenso (V-C2 / line shopping) usa TODOS os venues capturados; o VENUE do
        # cartão só pode ser uma casa EXECUTÁVEL (achado 6). As demais seguem em
        # `venues_comparados` como observação de consenso, nunca como venue do sinal.
        executaveis = [c for c in candidatos_venue
                       if _casa_executavel(c["casa"], politica, venues_executaveis)]
        melhor = melhor_preco(executaveis)
        if melhor is None:
            motivo = ("sem venue capturado" if not candidatos_venue
                      else "venues capturados, nenhum executável (allowlist)")
            resumo.pulados.append(f"{grupo.evento_id}/{grupo.mercado}/{sel}: {motivo}")
            continue

        # Anti-duplicidade (achado 7): se já há um sinal ABERTO para este candidato,
        # não reprocessa — nem novo sinal, nem novo aborto. Uma oportunidade
        # persistente não vira um sinal por minuto; a re-checagem/expiração é do L3.
        chave = chave_candidato(grupo.evento_id, grupo.mercado, grupo.linha, sel, melhor["casa_id"])
        if chave in chaves_abertas:
            resumo.pulados.append(f"{grupo.evento_id}/{grupo.mercado}/{sel}: sinal já aberto (dedup)")
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

        # P0.4 — estabilidade da referência é AFIRMAÇÃO, não default. Sem duas
        # revisões distintas na janela não há como dizer que a referência está
        # parada: antes, `variacao_pct` devolvia 0.0 sem histórico e `abs(0.0) <
        # limiar` declarava "estável". Dado ausente virava confirmação positiva —
        # o oposto de P6. Agora a indeterminação ABORTA o candidato.
        if not move_ref.mensuravel:
            _abortar_dedup(banco, grupo, sel, "referencia_estabilidade_indeterminada",
                           {"evento_id": grupo.evento_id, "mercado": grupo.mercado,
                            "selecao": sel, "linha": grupo.linha,
                            "gatilho": "value_bet",
                            "revisoes_na_janela": move_ref.revisoes},
                           0.0, gates, resumo, chave=chave, chaves_abortos=chaves_abortos,
                           rastrear_forcado=False)
            continue
        referencia_estavel = abs(move_ref.pct) < anomalia_lim

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
            _abortar_dedup(banco, grupo, sel, veredito.gate_reprovado, dossie_parcial,
                           edge, gates, resumo, chave=chave, chaves_abortos=chaves_abortos)
            continue

        # Gate de exposição em camadas (agregado).
        tetos = tetos_exposicao(gates, banca)
        vexp = avaliar_exposicao(stake_valor, exposto, tetos)
        if not vexp.aprovado:
            _abortar_dedup(banco, grupo, sel, vexp.gate_reprovado, dossie_parcial,
                           edge, gates, resumo, chave=chave, chaves_abortos=chaves_abortos)
            continue

        # Passou TODOS os gates mecânicos. Homologação (Doutrina P2 / achado 8):
        # mercado em 'backtest' (calibração) → candidato_sombra — rastreado até o
        # fechamento SÓ para medir CLV (alimenta E6.4), nunca sinal nem cartão. Só
        # mercado 'homologado' é enfileirado como sinal (segue para L2/L3).
        if modo_sombra_homolog:
            _registrar_candidato_sombra(banco, grupo, sel, dossie_parcial, resumo,
                                        chave=chave, chaves_abortos=chaves_abortos)
            continue

        # Homologado → dossiê completo + fila do L2.
        odd_min = odd_minima_aceitavel(p_justa, comissao, edge_min_frac)
        dossie = _montar_dossie(
            grupo=grupo, sel=sel, gatilho=gatilho, gatilho_anomalo=anomalo, caminho=caminho,
            p_justa=p_justa, odd_ref=odd_ref, melhor=melhor, edge=edge, comissao=comissao,
            stake_frac=stake_frac, odd_min=odd_min, ts_ref=ts_ref, janela_sinc=janela_sinc,
            referencia_estavel=referencia_estavel, serie_ref=serie_ref, serie_venue=serie_venue,
            candidatos_venue=candidatos_venue, exposto=exposto, liquidez_disp=liquidez_disp,
            aplica_liquidez=aplica_liquidez, politica=politica, banca_origem=banca_origem,
        )
        try:
            ret = enfileirar_sinal(banco, dossie, evento_id=grupo.evento_id,
                                   casa_venue_id=melhor["casa_id"], linha=grupo.linha,
                                   chave_candidato=chave)
        except Exception as e:  # backstop de unicidade do banco (corrida entre daemons)
            if _e_duplicidade(e):
                resumo.pulados.append(
                    f"{grupo.evento_id}/{grupo.mercado}/{sel}: sinal duplicado no banco (corrida)")
                chaves_abertas.add(chave)
                continue
            raise
        # P0.5: a RPC recusa SEM levantar quando o candidato já foi registrado no
        # evento (mesmo já vetado/expirado). Contar isso como sinal novo colocaria a
        # MESMA aposta duas vezes na amostra — o erro que a unicidade global corrige.
        if isinstance(ret, dict) and ret.get("criado") is False:
            resumo.pulados.append(
                f"{grupo.evento_id}/{grupo.mercado}/{sel}: candidato já registrado no evento")
            chaves_abertas.add(chave)
            continue
        chaves_abertas.add(chave)  # dedup intra-ciclo (não reemite no mesmo ciclo)
        resumo.sinais += 1
        _log.info("sinal enfileirado", extra={"evento": grupo.evento_id, "mercado": grupo.mercado,
                                               "selecao": sel, "gatilho": gatilho, "caminho": caminho,
                                               "edge_pct": round(edge * 100, 2)})


def _registrar(banco, grupo, sel, gate_reprovado, dossie_parcial, edge, gates, resumo: ResumoL1,
               *, chave: Optional[str] = None, rastrear_forcado: Optional[bool] = None) -> None:
    # `rastrear_forcado` sobrepõe o critério de near-miss: o candidato_sombra (achado 8)
    # é SEMPRE rastreado (True); os marcadores de não-autorização, nunca (False). Quando
    # None, vale a regra do near-miss (edge logo abaixo do gate — E2.6).
    if rastrear_forcado is None:
        rastrear = gate_reprovado == "edge_min_pct" and deve_rastrear_clv(edge, gates)
    else:
        rastrear = rastrear_forcado
    ret = registrar_aborto(banco, gatilho=dossie_parcial["gatilho"],
                           gate_reprovado=gate_reprovado or "desconhecido",
                           dossie_parcial=dossie_parcial, evento_id=grupo.evento_id,
                           clv_rastrear=rastrear, chave_candidato=chave)
    # P0.6: idem para abortos/candidato_sombra — uma unidade por aposta lógica.
    if isinstance(ret, dict) and ret.get("criado") is False:
        return
    resumo.abortos += 1
    if rastrear:
        resumo.rastreados_clv += 1


def _abortar_dedup(banco, grupo, sel, gate_reprovado, dossie_parcial, edge, gates, resumo: ResumoL1,
                   *, chave: str, chaves_abortos: set[str],
                   rastrear_forcado: Optional[bool] = None) -> None:
    """Registra o aborto SÓ se este candidato ainda não foi abortado na janela
    (achado 7): um near-miss persistente não vira um aborto por minuto."""
    if chave in chaves_abortos:
        resumo.pulados.append(f"{grupo.evento_id}/{grupo.mercado}/{sel}: aborto já registrado (dedup)")
        return
    _registrar(banco, grupo, sel, gate_reprovado, dossie_parcial, edge, gates, resumo,
               chave=chave, rastrear_forcado=rastrear_forcado)
    chaves_abortos.add(chave)


def _registrar_candidato_sombra(banco, grupo, sel, dossie_parcial, resumo: ResumoL1,
                                *, chave: str, chaves_abortos: set[str]) -> None:
    """candidato_sombra (achado 8): passou TODOS os gates mecânicos, mas o mercado
    (liga, mercado) está em 'backtest' (calibração) — não homologado. Registrado em
    `abortos_l1` com gate='mercado_nao_homologado' e clv_rastrear=True: acompanhado ATÉ
    o fechamento SÓ para medir CLV (alimenta E6.4 / homologação). JAMAIS vira sinal,
    status 'confirmado' ou cartão de execução (Doutrina P2). Deduplicado como qualquer
    aborto (achado 7) — um candidato persistente não vira um registro por ciclo."""
    if chave in chaves_abortos:
        resumo.pulados.append(
            f"{grupo.evento_id}/{grupo.mercado}/{sel}: candidato_sombra já registrado (dedup)")
        return
    _registrar(banco, grupo, sel, "mercado_nao_homologado", dossie_parcial, 0.0, None,
               resumo, chave=chave, rastrear_forcado=True)
    chaves_abortos.add(chave)
    resumo.candidatos_sombra += 1


def _marcar_grupo_nao_autorizado(banco, grupo, status: Optional[str], resumo: ResumoL1,
                                 *, chaves_abortos: set[str]) -> None:
    """Mercado (liga, mercado) NÃO autorizado a operar (Doutrina P2 / achado 8):
    'suspenso'/'caducado' (retirada/expiração explícita) ou SEM linha em
    `mercados_homologados`. A ausência é FALHA DE CONFIGURAÇÃO — fail-loud, jamais
    licença implícita para calibrar. Em nenhum dos casos se roda gate ou se gera
    candidato: o grupo é pulado e um marcador único (dedup por grupo) vai ao log de
    abortos (P7). Não rastreia CLV (não é oportunidade a medir, é bloqueio de escopo)."""
    liga = grupo.evento.get("liga", "")
    if status in ("suspenso", "caducado"):
        gate = f"mercado_{status}"
        _log.warning("mercado não homologado (retirado) — L1 não opera (P2)",
                     extra={"liga": liga, "mercado": grupo.mercado, "status": status})
    else:
        gate = "mercado_nao_configurado"
        _log.warning("mercado SEM homologação configurada — falha de configuração (P2), "
                     "não é licença implícita para calibrar",
                     extra={"liga": liga, "mercado": grupo.mercado})
    chave_grupo = f"{grupo.evento_id}|{grupo.mercado}|__homolog__|{gate}"
    if chave_grupo in chaves_abortos:
        resumo.pulados.append(f"{grupo.evento_id}/{grupo.mercado}: {gate} (dedup)")
        return
    registrar_aborto(
        banco, gatilho="homologacao", gate_reprovado=gate,
        dossie_parcial={"evento_id": grupo.evento_id, "mercado": grupo.mercado,
                        "linha": grupo.linha, "liga": liga, "status_homologacao": status},
        evento_id=grupo.evento_id, clv_rastrear=False, chave_candidato=chave_grupo,
    )
    chaves_abortos.add(chave_grupo)
    resumo.abortos += 1
    resumo.nao_autorizados += 1


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
        chave = (s["evento_id"], s["mercado"], linha_key(s.get("linha")))
        g = tmp.setdefault(chave, {"ref": {}, "venue": {}, "snaps_ref": []})
        sel = s["selecao"]
        odd = float(s["odd"])
        if casa.get("tipo") == "referencia":
            g["ref"].setdefault(sel, []).append((ts, odd))
            g["snaps_ref"].append(s)          # cru: base da revisão indivisível (P0.3)
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
            snaps_ref=g["snaps_ref"], inicio_utc=_dt(ev.get("inicio_utc")),
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


def _venues_executaveis(banco: Any) -> Optional[set[str]]:
    """Allowlist das casas onde o Daniel EXECUTA (`config_sistema.venues_executaveis`,
    lista JSON de chaves de bookmaker — ex.: `[\"bet365_br\", \"betano\"]`). Curada por
    ele (achado 6, ligada à decisão D3): o L1 nunca inventa que uma casa é executável.
    None = allowlist ausente/malformada → no modo sombra nenhuma casa vira venue do
    cartão (fail-closed). Não afeta o consenso (todas as casas seguem no line shopping)."""
    ler = getattr(banco, "config_vigente", None)
    if ler is None:
        return None
    doc = ler("venues_executaveis")
    if not doc or not doc.get("valor"):
        return None
    try:
        lista = json.loads(doc["valor"])
    except (json.JSONDecodeError, TypeError):
        _log.warning("venues_executaveis na config_sistema não é JSON válido — ignorada")
        return None
    if not isinstance(lista, list):
        _log.warning("venues_executaveis na config_sistema não é uma lista — ignorada")
        return None
    return {str(k).strip() for k in lista if str(k).strip()}


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

    # Allowlist de venues executáveis (achado 6): só casas onde o Daniel aposta
    # viram o venue do cartão no modo sombra. EXCHANGE não usa (venue = exchange).
    venues_executaveis = _venues_executaveis(banco)
    if politica is PoliticaVenue.RETAIL_SOMBRA and not venues_executaveis:
        _log.warning("modo sombra sem allowlist de venues executáveis "
                     "(config_sistema.venues_executaveis) — nenhum sinal será emitido (achado 6)")

    # Anti-duplicidade (achado 7): candidatos com sinal ABERTO (aguardando_crivo|
    # confirmado) e candidatos já abortados na janela não são reprocessados. Guardas
    # `getattr` toleram fakes/bancos sem os métodos (degradação: sem dedup, mas roda).
    _abertas = getattr(banco, "chaves_sinais_abertos", None)
    chaves_abertas: set[str] = set(_abertas()) if _abertas else set()
    _abortos = getattr(banco, "chaves_abortos_desde", None)
    chaves_abortos: set[str] = set(_abortos(desde)) if _abortos else set()

    # Gate de homologação de mercado (Doutrina P2 / achado 8): só mercado 'homologado'
    # gera sinal; 'backtest' vira candidato_sombra (só CLV). Mapa ausente (banco sem o
    # método) → {} → TODO mercado cai em "falha de configuração" (fail-closed: P2 não
    # autoriza calibração implícita — nenhum sinal sem homologação declarada).
    _homolog = getattr(banco, "homologacao_mercados", None)
    homolog: dict[tuple[str, str], str] = _homolog() if _homolog else {}

    exposicao_aberta = banco.exposicao_aberta()
    grupos = agrupar_snapshots(snaps, casas, eventos)
    for grupo in grupos:
        resumo.grupos += 1
        ev = eventos.get(grupo.evento_id, {})
        dia = str((_dt(ev.get("inicio_utc")) or agora).date())
        exposto = _exposto_do_evento(exposicao_aberta, grupo.evento_id, ev.get("liga", ""), dia)
        avaliar_grupo(banco, grupo, casas, gates, banca=banca, banca_origem=banca_origem,
                      exposto=exposto, agora=agora, politica=politica,
                      venues_executaveis=venues_executaveis, homolog=homolog,
                      chaves_abertas=chaves_abertas, chaves_abortos=chaves_abortos, resumo=resumo)

    banco.pulsar(DAEMON, {"grupos": resumo.grupos, "sinais": resumo.sinais,
                          "abortos": resumo.abortos, "rastreados_clv": resumo.rastreados_clv,
                          "candidatos_sombra": resumo.candidatos_sombra,
                          "nao_autorizados": resumo.nao_autorizados,
                          "politica_venue": politica.value, "banca_origem": banca_origem})
    _log.info("ciclo L1 concluído", extra={"grupos": resumo.grupos, "sinais": resumo.sinais,
                                           "abortos": resumo.abortos})
    return resumo
