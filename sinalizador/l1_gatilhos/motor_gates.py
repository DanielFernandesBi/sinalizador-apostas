"""Motor de gates do L1 (E2.2) — determinístico e SEM IA (regra 2).

Avalia um candidato a sinal contra os gates VIGENTES (lidos da tabela via
`CarregadorGates` — regra 6, nunca hard-coded). Retorna o PRIMEIRO gate
reprovado (para `abortos_l1.gate_reprovado`) ou aprovação.

Gates avaliados aqui (todos definidos na Doutrina §4 / seed do schema):
  - janela_sincronia_s     sincronia entre as capturas de referência e venue
  - referencia_estavel_ok  estabilidade da referência (flag booleana do L0)
  - snapshot_idade_max_s   idade do snapshot mais velho
  - odd_teto               teto de odd do venue
  - edge_min_pct           edge líquido mínimo
  - liquidez_multiplo_stake  liquidez disponível vs. stake

Exposição (tetos por jogo/liga/dia) é avaliada por `avaliar_exposicao`, à parte,
porque depende de dados agregados (`vw_exposicao_total`: dinheiro real em risco +
posição de papel + sinal em voo — Sugestão nº 13). Os tetos absolutos vêm
dos gates exposicao_max_jogo/liga_dia/dia_pct (Sugestão nº 3) via `tetos_exposicao`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol

from sinalizador.comum.tempo import idade_s


class ProvedorGates(Protocol):
    def get(self, nome: str): ...  # retorna Decimal (ver comum/gates.py)


@dataclass(frozen=True)
class ContextoAvaliacao:
    """Insumos de um candidato a sinal no momento da avaliação."""
    odd_venue: float
    edge_liquido: float            # FRAÇÃO (ex.: 0.03 = 3%)
    stake_valor: float             # valor absoluto do stake sugerido
    liquidez_disponivel: float     # `disponivel_no_preco` no venue
    ts_fonte_referencia: datetime  # carimbo DA FONTE (nunca now())
    ts_fonte_venue: datetime
    referencia_estavel_ok: bool
    agora: datetime                # relógio de avaliação (para idade do snapshot)


@dataclass(frozen=True)
class ResultadoGate:
    aprovado: bool
    gate_reprovado: Optional[str] = None
    detalhe: Optional[str] = None


def _seg(a: datetime, b: datetime) -> float:
    return (a - b).total_seconds()


def avaliar(
    ctx: ContextoAvaliacao, gates: ProvedorGates, *, avaliar_liquidez: bool = True
) -> ResultadoGate:
    """Avalia os gates de integridade/valor. Retorna o primeiro reprovado.

    `avaliar_liquidez` só é False quando o venue NÃO tem book de exchange (varejo):
    o gate `liquidez_multiplo_stake` é um conceito de exchange (profundidade para o
    stake sem mover o preço) e é inaplicável a casa de varejo. O chamador decide —
    e registra a inaplicabilidade no dossiê; nunca se inventa um valor de liquidez
    para "passar" o gate (isso seria fabricar dado, P6).
    """
    # 1) Sincronia entre as fontes (edge fantasma por dessincronia — Correção #1).
    janela = float(gates.get("janela_sincronia_s"))
    desassincronia = abs(_seg(ctx.ts_fonte_venue, ctx.ts_fonte_referencia))
    if desassincronia > janela:
        return ResultadoGate(False, "janela_sincronia_s",
                             f"dessincronia {desassincronia:.1f}s > {janela:.0f}s")

    # 2) Estabilidade da referência (flag do L0; não é gate numérico).
    if not ctx.referencia_estavel_ok:
        return ResultadoGate(False, "referencia_estavel",
                             "referência em movimento no momento da captura")

    # 3) Idade do snapshot mais velho. `idade_s` devolve None para carimbo no FUTURO
    #    (P1.8): a subtração daria negativa e passaria em qualquer teto — o dado mais
    #    suspeito do lote seria o que menos apanha.
    idade_max = float(gates.get("snapshot_idade_max_s"))
    idade_ref = idade_s(ctx.agora, ctx.ts_fonte_referencia)
    idade_venue = idade_s(ctx.agora, ctx.ts_fonte_venue)
    if idade_ref is None or idade_venue is None:
        return ResultadoGate(False, "carimbo_no_futuro",
                             "carimbo de fonte adiante do relógio — dado inconsistente")
    idade = max(idade_ref, idade_venue)
    if idade > idade_max:
        return ResultadoGate(False, "snapshot_idade_max_s",
                             f"idade {idade:.1f}s > {idade_max:.0f}s")

    # 4) Teto de odd (viés estrutural contra odds altas — P3).
    odd_teto = float(gates.get("odd_teto"))
    if ctx.odd_venue > odd_teto:
        return ResultadoGate(False, "odd_teto",
                             f"odd {ctx.odd_venue} > teto {odd_teto}")

    # 5) Edge líquido mínimo. Gate em PONTOS PERCENTUAIS; edge é fração.
    edge_min_pct = float(gates.get("edge_min_pct"))
    if ctx.edge_liquido * 100.0 < edge_min_pct:
        return ResultadoGate(False, "edge_min_pct",
                             f"edge {ctx.edge_liquido*100:.2f}% < mínimo {edge_min_pct:.2f}%")

    # 6) Liquidez suficiente para o stake sem mover o preço (só p/ venue de exchange).
    if avaliar_liquidez:
        mult = float(gates.get("liquidez_multiplo_stake"))
        exigida = mult * ctx.stake_valor
        if ctx.liquidez_disponivel < exigida:
            return ResultadoGate(False, "liquidez_multiplo_stake",
                                 f"liquidez {ctx.liquidez_disponivel} < {mult:.0f}× stake ({exigida})")

    return ResultadoGate(True, None, None)


def stake_kelly_fracao(p_justa: float, odd_venue: float, gates: ProvedorGates,
                       *, comissao: float = 0.0) -> float:
    """Stake como FRAÇÃO da banca por Kelly fracionário, com teto absoluto (P5).

    Kelly é `f = (p·b − q) / b`, onde **b é o ganho LÍQUIDO por unidade apostada** e
    `q = 1 − p`. Numa exchange a comissão come parte do ganho, então o b real é
    `(odd − 1) × (1 − comissão)` — usar `odd − 1` presumiria um prêmio maior que o
    efetivamente recebido e **superdimensionaria o stake** (P1.9).

    Com `comissao = 0` (varejo de odd fixa, o regime ratificado pela Sugestão nº 6)
    a fórmula colapsa EXATAMENTE na anterior — `(p·odd − 1)/(odd − 1)` —, então o
    modo sombra não muda de comportamento. A correção só morde onde há comissão.

    Slippage ainda NÃO entra aqui: em odd fixa é zero por definição (Doutrina
    §-sombra) e não existe estimador para exchange enquanto não houver book
    capturável (PC-SLIPPAGE). Somar um zero inventado seria fingir precisão.

    Aplica `kelly_fracao` (¼) e limita a `stake_max_pct`. Edge não-positivo → 0.
    """
    if not 0.0 <= p_justa <= 1.0:
        raise ValueError(f"p_justa fora de [0,1]: {p_justa}")
    if odd_venue <= 1.0:
        raise ValueError(f"odd_venue deve ser > 1.0: {odd_venue}")
    if not 0.0 <= comissao < 1.0:
        raise ValueError(f"comissao fora de [0,1): {comissao}")
    b = (odd_venue - 1.0) * (1.0 - comissao)
    if b <= 0.0:
        return 0.0
    kelly_pleno = (p_justa * b - (1.0 - p_justa)) / b
    if kelly_pleno <= 0.0:
        return 0.0
    fracao = kelly_pleno * float(gates.get("kelly_fracao"))
    teto = float(gates.get("stake_max_pct")) / 100.0  # stake_max_pct é percentual
    return min(fracao, teto)


# Gates de exposição (Sugestão nº 3) → nível lógico usado por avaliar_exposicao.
_GATE_EXPOSICAO = {
    "jogo": "exposicao_max_jogo_pct",
    "liga_dia": "exposicao_max_liga_dia_pct",
    "dia": "exposicao_max_dia_pct",
}


def tetos_exposicao(gates: ProvedorGates, banca: float) -> dict[str, float]:
    """Tetos absolutos de exposição por camada = (gate_pct / 100) × banca.

    Os percentuais vêm da tabela `gates` (regra 6): exposicao_max_jogo_pct,
    _liga_dia_pct, _dia_pct. Nunca constante.
    """
    return {
        nivel: float(gates.get(gate)) / 100.0 * banca
        for nivel, gate in _GATE_EXPOSICAO.items()
    }


def avaliar_exposicao(
    stake_valor: float,
    exposto: dict[str, float],
    tetos: dict[str, float],
) -> ResultadoGate:
    """Avalia a exposição AGREGADA por jogo/liga-dia/dia (`vw_exposicao_total`).

    `exposto` e `tetos` mapeiam níveis ('jogo', 'liga_dia', 'dia') → valor. Monte
    `tetos` com `tetos_exposicao(gates, banca)`. Só os níveis presentes em `tetos`
    são checados. Reprova se exposto + stake ultrapassa o teto do nível.
    """
    for nivel, teto in tetos.items():
        atual = exposto.get(nivel, 0.0)
        if atual + stake_valor > teto:
            return ResultadoGate(False, f"exposicao_{nivel}",
                                 f"exposição {nivel} {atual}+{stake_valor} > teto {teto}")
    return ResultadoGate(True, None, None)
