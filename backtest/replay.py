"""Replay do L1 sobre o histórico + medição de CLV (E6.2).

Para cada partida e cada mercado:
  1. p_justa = Shin(referência Pinnacle na ABERTURA) por seleção — só decisão;
  2. odd_venue = melhor preço entre as demais casas na ABERTURA — só decisão;
  3. edge_liquido (Doutrina §3; no backtest o venue é varejo → comissão 0 e
     slippage 0, pois não há dado de exchange/liquidez — declarado no relatório);
  4. candidato de value_bet quando edge > 0;
  5. MEDIÇÃO: CLV pela MESMA função da produção (`l4_fechamento.clv.clv_pct`,
     em PONTOS PERCENTUAIS), com p_fechamento = Shin(Pinnacle no FECHAMENTO).

ZERO LOOK-AHEAD: o passo de decisão (1–4) usa apenas colunas de abertura. O
fechamento entra somente na medição (5); o resultado do jogo não entra em lugar
nenhum da decisão nem do CLV.

Célula = liga × mercado × LINHA × faixa de odd — a mesma unidade que
`mercados_homologados` representa desde a migration 0020, para que a evidência
consiga alcançar a autorização. Célula com n < amostra mínima (200, P12) é
reportada como "amostra insuficiente", sem conclusão; `significante` (limite
inferior do IC95 acima de zero) é uma leitura ADICIONAL, com o erro padrão
agrupado por partida.
"""
from __future__ import annotations

import csv
import json
import math
from collections import defaultdict

from sinalizador.l1_gatilhos.devig import devig_shin
from sinalizador.l1_gatilhos.edge import edge_liquido
from sinalizador.comum.significancia import estatistica_agrupada
from sinalizador.l4_fechamento.clv import clv_pct as clv_pct_producao

from .ah import liquidar_ah
from .football_data import MERCADOS, num

# Gates PROVISÓRIOS da Doutrina §4 usados como definição de value_bet no backtest.
# São o objeto de calibração do E6.3 — parâmetros de análise, não gates operacionais
# (estes vêm da tabela `gates` em produção). Ficam parametrizáveis em replay().
EDGE_MIN_PROV = 0.02   # edge líquido mínimo (≥ 2,0%)
ODD_TETO_PROV = 3.30   # teto de odd
AMOSTRA_MINIMA = 200   # Doutrina P12 (pétreo): sem conclusão abaixo disso

# Faixas de odd (do venue). Fronteira em 3.30 casa com o teto provisório.
FAIXAS: tuple[tuple[float, float], ...] = (
    (1.01, 1.50), (1.50, 2.00), (2.00, 2.60),
    (2.60, 3.30), (3.30, 5.00), (5.00, float("inf")),
)


def faixa_odd(odd: float) -> str:
    for lo, hi in FAIXAS:
        if lo <= odd < hi:
            return f"{lo:.2f}+" if math.isinf(hi) else f"{lo:.2f}-{hi:.2f}"
    return "fora"


def _melhor_preco(row: dict, cols: tuple[str, ...]) -> float | None:
    precos = [num(row.get(c)) for c in cols]
    validos = [p for p in precos if p is not None and p > 1.0]
    return max(validos) if validos else None


def candidatos_da_partida(
    row: dict, *, edge_min: float = EDGE_MIN_PROV, odd_teto: float = ODD_TETO_PROV
) -> list[dict]:
    """Gera as linhas de candidato (edge > 0) de uma partida. Ver módulo p/ regras."""
    liga = row.get("_liga", "?")
    data = (row.get("Date") or "").strip()
    # Identificação da PARTIDA: é a unidade de agrupamento da significância. As
    # seleções de um mesmo jogo (H/D/A, over/under, os dois lados do AH) não são
    # observações independentes — saem do mesmo book, contra o mesmo fechamento.
    partida = f"{(row.get('HomeTeam') or '?').strip()} x {(row.get('AwayTeam') or '?').strip()}"
    linhas: list[dict] = []

    for mercado in MERCADOS:
        ref_ab = [num(row.get(s.col_ref_abertura)) for s in mercado.selecoes]
        ref_fe = [num(row.get(s.col_ref_fechamento)) for s in mercado.selecoes]

        # Referência de abertura incompleta → não há decisão possível (P6).
        if any(o is None or o <= 1.0 for o in ref_ab):
            continue
        # Fechamento incompleto → não há como MEDIR CLV → descarta o mercado.
        if any(o is None or o <= 1.0 for o in ref_fe):
            continue
        try:
            p_abertura, _z_ab = devig_shin(ref_ab)
            p_fechamento, _z_fe = devig_shin(ref_fe)
        except ValueError:
            continue  # book inválido (ex.: soma < 1) → pula, nunca chuta

        for i, sel in enumerate(mercado.selecoes):
            venue = _melhor_preco(row, sel.cols_venue)
            if venue is None:
                continue
            p_justa = p_abertura[i]
            # DECISÃO: comissão 0 e slippage 0 (venue de varejo no backtest).
            edge = edge_liquido(p_justa, venue, 0.0, 0.0)
            if edge <= 0.0:
                continue  # candidato só quando o venue oferece valor vs. referência
            # MEDIÇÃO (não participa da decisão). A unidade é a MESMA da produção
            # (`l4_fechamento.clv.clv_pct`, em pontos percentuais) porque é a mesma
            # função: antes o backtest calculava `venue·p − 1` num campo também
            # chamado `clv_pct`, ou seja, o mesmo nome com unidades diferentes por
            # um fator 100 — a forma mais silenciosa possível de errar uma importação.
            clv = clv_pct_producao(venue, p_fechamento[i])
            linhas.append({
                "liga": liga,
                "mercado": mercado.nome,
                "selecao": sel.codigo,
                "linha": mercado.linha,
                "partida": partida,
                "data": data,
                "faixa_odd": faixa_odd(venue),
                "p_justa_abertura": p_justa,
                "odd_venue": venue,
                "edge_liquido": edge,
                "p_ref_fechamento": p_fechamento[i],
                "clv_pct": clv,
                "value_bet_provisional": bool(edge >= edge_min and venue <= odd_teto),
                "resultado_ah": None,
            })

    linhas.extend(_candidatos_ah(row, liga, data, partida,
                                 edge_min=edge_min, odd_teto=odd_teto))
    return linhas


# Colunas Football-Data do Asian Handicap (Pinnacle abertura/fechamento + linha).
_AH_LADOS = (("mandante", "PAHH", "PAHCH", "B365AHH"),
             ("visitante", "PAHA", "PAHCA", "B365AHA"))


def _candidatos_ah(
    row: dict, liga: str, data: str, partida: str, *, edge_min: float, odd_teto: float
) -> list[dict]:
    """AH via Pinnacle (abertura PAH* / fechamento PAHC*), venue = B365 (única casa
    de varejo consistente). CLV só é medido quando a LINHA de abertura == fechamento
    (comparar handicaps diferentes seria a armadilha do V-A6). `resultado_ah` é
    informativo (liquidação por decomposição) — não entra na decisão nem no CLV.
    """
    linha_ab = num(row.get("AHh"))
    linha_fe = num(row.get("AHCh"))
    if linha_ab is None or linha_fe is None or linha_ab != linha_fe:
        return []

    ref_ab = [num(row.get("PAHH")), num(row.get("PAHA"))]
    ref_fe = [num(row.get("PAHCH")), num(row.get("PAHCA"))]
    if any(o is None or o <= 1.0 for o in ref_ab + ref_fe):
        return []
    try:
        p_ab, _z1 = devig_shin(ref_ab)
        p_fe, _z2 = devig_shin(ref_fe)
    except ValueError:
        return []

    gm, gv = num(row.get("FTHG")), num(row.get("FTAG"))
    saidas: list[dict] = []
    for i, (cod, _c_ab, _c_fe, col_venue) in enumerate(_AH_LADOS):
        venue = _melhor_preco(row, (col_venue,))
        if venue is None:
            continue
        edge = edge_liquido(p_ab[i], venue, 0.0, 0.0)
        if edge <= 0.0:
            continue
        resultado = None
        if gm is not None and gv is not None:
            resultado = liquidar_ah(linha_ab, cod, int(round(gm)), int(round(gv)))
        saidas.append({
            "liga": liga,
            "mercado": "ah",
            "selecao": cod,
            "linha": linha_ab,
            "partida": partida,
            "data": data,
            "faixa_odd": faixa_odd(venue),
            "p_justa_abertura": p_ab[i],
            "odd_venue": venue,
            "edge_liquido": edge,
            "p_ref_fechamento": p_fe[i],
            "clv_pct": clv_pct_producao(venue, p_fe[i]),
            "value_bet_provisional": bool(edge >= edge_min and venue <= odd_teto),
            "resultado_ah": resultado,
        })
    return saidas


def replay(
    partidas: list[dict], *, edge_min: float = EDGE_MIN_PROV, odd_teto: float = ODD_TETO_PROV
) -> list[dict]:
    """Roda o replay sobre uma lista de partidas; devolve todos os candidatos."""
    todos: list[dict] = []
    for row in partidas:
        todos.extend(candidatos_da_partida(row, edge_min=edge_min, odd_teto=odd_teto))
    return todos


def agregar_celulas(
    candidatos: list[dict], *, amostra_minima: int = AMOSTRA_MINIMA
) -> list[dict]:
    """Agrega os value_bets em células liga × mercado × LINHA × faixa de odd.

    A linha entra na chave porque é parte da identidade do mercado na produção
    (`odds_snapshots` guarda mercado e linha separados) e porque OU 2.5 e OU 3.5 são
    mercados diferentes — agregá-los sob "ou" misturaria evidências de coisas que
    não são a mesma coisa. Para o 1X2 a linha é `None` e a chave degenera na antiga.

    A célula devolvida é a MESMA unidade que `mercados_homologados` sabe representar
    (migration 0020): é isso que permite a evidência autorizar a operação.
    """
    grupos: dict[tuple, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for c in candidatos:
        if c["value_bet_provisional"]:
            chave = (c["liga"], c["mercado"], c.get("linha"), c["faixa_odd"])
            grupos[chave][c.get("partida") or f"?{id(c)}"].append(c["clv_pct"])

    celulas: list[dict] = []
    for (liga, merc, linha, faixa) in sorted(grupos, key=lambda k: tuple(str(x) for x in k)):
        # Mesma função que o E6.4 usará sobre o CLV de sombra (`comum/significancia`):
        # duas cópias da mesma conta divergem, e foi assim que `clv_pct` passou a
        # significar coisas diferentes nos dois lados.
        e = estatistica_agrupada(grupos[(liga, merc, linha, faixa)])
        # `suficiente` continua sendo a P12 (pétrea): sem 200 observações não se
        # conclui NADA. `significante` é uma leitura ADICIONAL e mais exigente — o
        # limite inferior do IC95 acima de zero. Qual das duas autoriza homologar é
        # decisão de rito (PC-SIGNIFICANCIA), não deste código.
        celulas.append({
            "liga": liga, "mercado": merc, "linha": linha, "faixa_odd": faixa,
            "n": e.n, "n_clusters": e.n_clusters,
            "clv_medio": e.media, "clv_desvio": e.desvio,
            "clv_medio_cluster": e.media_cluster, "erro_padrao": e.erro_padrao,
            "ic95_baixo": e.ic95_baixo, "ic95_alto": e.ic95_alto,
            "suficiente": e.n >= amostra_minima,
            "significante": e.significante,
        })
    return celulas


# ---------------- saídas (relatório legível + estruturado) ----------------

_CAMPOS_CANDIDATO = [
    "liga", "mercado", "selecao", "linha", "partida", "data", "faixa_odd",
    "p_justa_abertura", "odd_venue", "edge_liquido",
    "p_ref_fechamento", "clv_pct", "value_bet_provisional", "resultado_ah",
]
_CAMPOS_CELULA = ["liga", "mercado", "linha", "faixa_odd", "n", "n_clusters",
                  "clv_medio", "clv_desvio", "clv_medio_cluster", "erro_padrao",
                  "ic95_baixo", "ic95_alto", "suficiente", "significante"]


def _escrever_csv(caminho: str, campos: list[str], linhas: list[dict]) -> None:
    with open(caminho, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=campos)
        w.writeheader()
        for linha in linhas:
            w.writerow({k: linha.get(k) for k in campos})


def cabecalho_relatorio(meta: dict) -> str:
    """Cabeçalho que DECLARA as limitações do backtest (obrigatório)."""
    return f"""# Backtest do L1 (value_bet) — CLV por liga × mercado × faixa de odd

Gerado em: {meta.get('gerado_em', '?')}
Ligas: {', '.join(meta.get('ligas', []))}
Temporadas: {', '.join(meta.get('temporadas', []))}
Parâmetros (provisórios, Doutrina §4 — a calibrar): edge_min={meta.get('edge_min')}, odd_teto={meta.get('odd_teto')}

## Limitações declaradas (LEIA ANTES DE CONCLUIR)

- **Dois instantes, sem intradiário.** O Football-Data só traz abertura e
  fechamento. O replay usa: referência = Pinnacle na abertura (colunas PS*),
  de-vigada por Shin, vs. venue = melhor preço entre as demais casas na abertura;
  o CLV é medido contra o fechamento da Pinnacle (colunas PSC*). Movimento
  intradiário, odds_drop e re-checagem de preço NÃO são exercíveis aqui — isso é
  o que o **modo sombra** (E7) vai cobrir, não este backtest.
- **Zero look-ahead.** Nenhuma informação de fechamento ou de resultado do jogo
  participa da decisão simulada de value_bet — só da medição do CLV.
- **Venue de varejo sem custo.** Como não há dado de exchange/liquidez no
  dataset, o edge do backtest usa comissão 0 e slippage 0. Custos de execução
  (comissão da Betfair, slippage) são do modo sombra, não deste backtest.
- **OU 2.5 e AH com cobertura fina.** A única casa de varejo consistente no
  dataset é o Bet365, então "melhor preço entre as demais casas" degenera para o
  B365 nesses mercados. O 1X2 tem cobertura ampla de casas.
- **AH: CLV só na mesma linha.** O handicap AH é medido só quando a linha de
  abertura == fechamento (comparar handicaps diferentes seria falso). A coluna
  `resultado_ah` (liquidação por decomposição em meias-apostas) é INFORMATIVA —
  não entra na decisão nem no CLV (o KPI é o CLV, P8).
- **Amostra (P12).** Célula (liga × mercado × linha × faixa de odd) com
  n < {meta.get('amostra_minima', AMOSTRA_MINIMA)} aparece como **amostra insuficiente**, sem conclusão.
- **Significância.** O IC95 usa erro padrão AGRUPADO POR PARTIDA: as seleções de um
  mesmo jogo (H/D/A, over/under, os dois lados do AH) saem do mesmo book contra o
  mesmo fechamento e não são observações independentes. Tratá-las como livres
  encolheria o erro padrão e faria a célula parecer significante antes de ser.
  A coluna `jogos` é o tamanho de amostra que sustenta a conclusão; `n` é informativo.
- **O VENUE HISTÓRICO NÃO É O VENUE REAL — e nenhum código conserta isso.** O melhor
  preço aqui sai de casas europeias do Football-Data (e, em OU/AH, essencialmente do
  Bet365). Isso NÃO prova que o mesmo preço estava disponível na operação brasileira,
  na conta do Daniel, no mesmo produto, no mesmo instante e com os mesmos limites.
  O backtest calibra a HIPÓTESE (existe CLV nesta célula?); quem prova a execução é o
  MODO SOMBRA (E7), com preço real capturado do venue real. Homologar um mercado com
  base só neste relatório é afirmar sobre um mercado que não foi medido.
- **Brasileirão ausente.** O Football-Data não cobre o Brasileirão — lacuna
  registrada como pendência do D6 no PLANO_MVP.
"""


def _tabela_celulas(celulas: list[dict]) -> str:
    linhas = ["| liga | mercado | linha | faixa odd | n | jogos | CLV médio | IC95 | conclusão |",
              "|---|---|---:|---|---:|---:|---:|---|---|"]
    for c in celulas:
        if not c["suficiente"]:
            conclusao = "amostra insuficiente"
        elif c["significante"]:
            conclusao = "CLV > 0 com IC95"
        else:
            conclusao = "sem significância"
        if c["ic95_baixo"] is None:
            ic = "—"
        else:
            ic = f"[{c['ic95_baixo']:+.2f}%, {c['ic95_alto']:+.2f}%]"
        # `clv_*` JÁ estão em pontos percentuais (mesma unidade da produção): o
        # ×100 daqui era o par do campo que guardava fração — os dois sumiram juntos.
        linhas.append(
            f"| {c['liga']} | {c['mercado']} | {c['linha'] if c['linha'] is not None else '—'} | "
            f"{c['faixa_odd']} | {c['n']} | {c['n_clusters']} | "
            f"{c['clv_medio']:.2f}% | {ic} | {conclusao} |"
        )
    return "\n".join(linhas)


def escrever_saidas(candidatos: list[dict], celulas: list[dict], destino: str, *, meta: dict) -> None:
    """Escreve os 4 artefatos em `destino/`: relatório .md + CSV/JSON re-processáveis."""
    import os
    os.makedirs(destino, exist_ok=True)

    _escrever_csv(os.path.join(destino, "candidatos.csv"), _CAMPOS_CANDIDATO, candidatos)
    _escrever_csv(os.path.join(destino, "celulas.csv"), _CAMPOS_CELULA, celulas)
    with open(os.path.join(destino, "celulas.json"), "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "celulas": celulas}, f, ensure_ascii=False, indent=2)

    suf = [c for c in celulas if c["suficiente"]]
    corpo = [
        cabecalho_relatorio(meta),
        "\n## Resumo\n",
        f"- Candidatos (edge > 0): **{len(candidatos)}**",
        f"- value_bets provisórios: **{sum(1 for c in candidatos if c['value_bet_provisional'])}**",
        f"- Células: **{len(celulas)}** (com amostra suficiente: **{len(suf)}**)",
        "\n## Células — apenas value_bet provisório\n",
        "_Recorte: só candidatos com `value_bet_provisional = True` (edge ≥ edge_min "
        "**e** odd ≤ odd_teto). NÃO é o conjunto de candidatos (edge > 0) — este está "
        "completo em `candidatos.csv` para a re-varredura de gates do E6.3._\n",
        _tabela_celulas(celulas) if celulas else "_(nenhuma célula)_",
        "\n---\n*Dados estruturados re-processáveis pelo E6.3: `candidatos.csv`, "
        "`celulas.csv`, `celulas.json`.*\n",
    ]
    with open(os.path.join(destino, "relatorio.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(corpo))
