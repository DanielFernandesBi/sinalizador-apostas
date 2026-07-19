"""Replay do L1 sobre o histórico + medição de CLV (E6.2).

Para cada partida e cada mercado:
  1. p_justa = Shin(referência Pinnacle na ABERTURA) por seleção — só decisão;
  2. odd_venue = melhor preço entre as demais casas na ABERTURA — só decisão;
  3. edge_liquido (Doutrina §3; no backtest o venue é varejo → comissão 0 e
     slippage 0, pois não há dado de exchange/liquidez — declarado no relatório);
  4. candidato de value_bet quando edge > 0;
  5. MEDIÇÃO: CLV = odd_venue · p_fechamento − 1, com
     p_fechamento = Shin(referência Pinnacle no FECHAMENTO).

ZERO LOOK-AHEAD: o passo de decisão (1–4) usa apenas colunas de abertura. O
fechamento entra somente na medição (5); o resultado do jogo não entra em lugar
nenhum da decisão nem do CLV.

Célula = liga × mercado × faixa de odd. Célula com n < amostra mínima (200, P12)
é reportada como "amostra insuficiente", sem conclusão.
"""
from __future__ import annotations

import csv
import json
import math
from collections import defaultdict

from sinalizador.l1_gatilhos.devig import devig_shin
from sinalizador.l1_gatilhos.edge import edge_liquido

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
            # MEDIÇÃO (não participa da decisão):
            clv = venue * p_fechamento[i] - 1.0
            linhas.append({
                "liga": liga,
                "mercado": mercado.nome,
                "selecao": sel.codigo,
                "data": data,
                "faixa_odd": faixa_odd(venue),
                "p_justa_abertura": p_justa,
                "odd_venue": venue,
                "edge_liquido": edge,
                "p_ref_fechamento": p_fechamento[i],
                "clv_pct": clv,
                "value_bet_provisional": bool(edge >= edge_min and venue <= odd_teto),
            })
    return linhas


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
    """Agrega os value_bets provisórios em células liga × mercado × faixa de odd."""
    grupos: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for c in candidatos:
        if c["value_bet_provisional"]:
            grupos[(c["liga"], c["mercado"], c["faixa_odd"])].append(c["clv_pct"])

    celulas: list[dict] = []
    for (liga, merc, faixa), clvs in sorted(grupos.items()):
        n = len(clvs)
        media = sum(clvs) / n
        desvio = math.sqrt(sum((x - media) ** 2 for x in clvs) / n) if n > 1 else 0.0
        celulas.append({
            "liga": liga,
            "mercado": merc,
            "faixa_odd": faixa,
            "n": n,
            "clv_medio": media,
            "clv_desvio": desvio,
            "suficiente": n >= amostra_minima,
        })
    return celulas


# ---------------- saídas (relatório legível + estruturado) ----------------

_CAMPOS_CANDIDATO = [
    "liga", "mercado", "selecao", "data", "faixa_odd",
    "p_justa_abertura", "odd_venue", "edge_liquido",
    "p_ref_fechamento", "clv_pct", "value_bet_provisional",
]
_CAMPOS_CELULA = ["liga", "mercado", "faixa_odd", "n", "clv_medio", "clv_desvio", "suficiente"]


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
- **OU 2.5 com cobertura fina.** A única casa de varejo consistente no dataset é
  o Bet365, então "melhor preço entre as demais casas" degenera para o B365 nesse
  mercado. O 1X2 tem cobertura ampla de casas.
- **Amostra (P12).** Célula (liga × mercado × faixa de odd) com n < {meta.get('amostra_minima', AMOSTRA_MINIMA)}
  aparece como **amostra insuficiente**, sem conclusão.
- **Brasileirão ausente.** O Football-Data não cobre o Brasileirão — lacuna
  registrada como pendência do D6 no PLANO_MVP.
"""


def _tabela_celulas(celulas: list[dict]) -> str:
    linhas = ["| liga | mercado | faixa odd | n | CLV médio | desvio | conclusão |",
              "|---|---|---|---:|---:|---:|---|"]
    for c in celulas:
        conclusao = "OK" if c["suficiente"] else "amostra insuficiente"
        linhas.append(
            f"| {c['liga']} | {c['mercado']} | {c['faixa_odd']} | {c['n']} | "
            f"{c['clv_medio']*100:.2f}% | {c['clv_desvio']*100:.2f}% | {conclusao} |"
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
        "\n## Células (value_bet provisório)\n",
        _tabela_celulas(celulas) if celulas else "_(nenhuma célula)_",
        "\n---\n*Dados estruturados re-processáveis pelo E6.3: `candidatos.csv`, "
        "`celulas.csv`, `celulas.json`.*\n",
    ]
    with open(os.path.join(destino, "relatorio.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(corpo))
