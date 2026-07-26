"""Significância de uma célula de CLV — UMA definição, usada pelos dois lados.

Existe aqui, e não dentro do backtest, pela mesma razão que `clv_pct` passou a ser
importado da produção: o backtest calcula significância sobre o histórico e o E6.4
vai calcular significância sobre o CLV de sombra, e **duas cópias da mesma conta
divergem** — foi exatamente assim que o mesmo campo `clv_pct` passou a significar
fração de um lado e ponto percentual do outro. Um dono só, desde o começo.

O erro padrão é AGRUPADO (clustered) pela unidade de jogo. As observações de uma
célula não são independentes: as três seleções de um 1X2 saem do mesmo book contra o
mesmo fechamento, over e under são o mesmo book visto dos dois lados, e os dois lados
do AH idem. Tratar cada seleção como observação livre infla o n efetivo, encolhe o
erro padrão e faz a célula parecer significante antes de ser — e o viés é SEMPRE na
direção de concluir cedo demais, que é o lado errado para errar quando a conclusão
autoriza dinheiro real (E7).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Optional

# z de 95% bilateral. Normal, não t: a amostra mínima da P12 é 200 observações, e o
# número de CLUSTERS numa célula que chega lá passa de 30 com folga, faixa em que
# t ≈ z. Concluir sobre célula com poucos clusters exigiria t de Student.
Z95 = 1.959964


@dataclass(frozen=True)
class Estatistica:
    n: int                          # observações (P12 conta ISTO — ver doutrina)
    n_clusters: int                 # jogos distintos: o que sustenta a conclusão
    media: float
    desvio: float
    media_cluster: float
    erro_padrao: Optional[float]    # None = um cluster só (não há dispersão a medir)
    ic95_baixo: Optional[float]
    ic95_alto: Optional[float]

    @property
    def significante(self) -> bool:
        """IC95 inteiro acima de zero. Média positiva NÃO basta: média positiva com
        intervalo cruzando o zero é compatível com CLV verdadeiro negativo."""
        return self.ic95_baixo is not None and self.ic95_baixo > 0.0


def estatistica_agrupada(clvs_por_grupo: dict[str, list[float]]) -> Estatistica:
    """Estatística da célula, com erro padrão agrupado pela chave do dicionário.

    A chave é a unidade de independência — a PARTIDA. Quem monta o dicionário decide
    o que é um jogo; esta função só se recusa a fingir que sabe mais do que os
    clusters permitem.
    """
    todos = [x for xs in clvs_por_grupo.values() for x in xs]
    if not todos:
        raise ValueError("célula sem observações")
    n = len(todos)
    media = sum(todos) / n
    desvio = math.sqrt(sum((x - media) ** 2 for x in todos) / n) if n > 1 else 0.0

    medias = [sum(xs) / len(xs) for xs in clvs_por_grupo.values()]
    k = len(medias)
    media_cluster = sum(medias) / k
    if k > 1:
        var = sum((m - media_cluster) ** 2 for m in medias) / (k - 1)
        erro_padrao: Optional[float] = math.sqrt(var / k)
        ic_baixo: Optional[float] = media_cluster - Z95 * erro_padrao
        ic_alto: Optional[float] = media_cluster + Z95 * erro_padrao
    else:
        # Um cluster só: não há de onde tirar dispersão. `None`, nunca 0.0 — erro
        # padrão zero afirmaria certeza absoluta a partir de nada (P6).
        erro_padrao = ic_baixo = ic_alto = None

    return Estatistica(n=n, n_clusters=k, media=media, desvio=desvio,
                       media_cluster=media_cluster, erro_padrao=erro_padrao,
                       ic95_baixo=ic_baixo, ic95_alto=ic_alto)


def homologavel(est: Estatistica, *, amostra_minima: int) -> bool:
    """Critério de homologação (Sugestão nº 16): P12 **e** IC95 acima de zero.

    Os dois, nunca um só. `n >= amostra_minima` é a P12, que é PÉTREA e mede TAMANHO
    — uma célula com n=5000 e média +0,1% ruidosa a satisfaz. O IC95 mede EVIDÊNCIA,
    mas sozinho poderia concluir sobre amostra pequena e de baixa variância, o que a
    P12 proíbe. Na prática o IC95 é o vínculo que morde; a P12 é o piso que não se
    negocia.

    Isto responde se a célula tem CLV comprovado. NÃO responde se o preço estava
    disponível para o operador — essa é a pergunta do modo sombra, e a Sugestão nº 16
    exige que a evidência venha de lá (ver PC-VENUE-HISTORICO).
    """
    return est.n >= amostra_minima and est.significante
