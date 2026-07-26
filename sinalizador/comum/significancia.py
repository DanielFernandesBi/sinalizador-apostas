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

# Nível por célula. 0.025 UNILATERAL é exatamente o mesmo teste que "limite inferior
# do IC95 bilateral acima de zero" — só nos interessa CLV > 0, e essa equivalência é
# o que permite falar em valor-p sem mudar o critério de célula única.
ALFA_CELULA = 0.025


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
        intervalo cruzando o zero é compatível com CLV verdadeiro negativo.

        Vale para uma célula OLHADA SOZINHA. Ao escolher entre muitas, use
        `homologaveis` — ver a nota de multiplicidade lá.
        """
        return self.ic95_baixo is not None and self.ic95_baixo > 0.0

    @property
    def valor_p(self) -> Optional[float]:
        """p unilateral de H0: CLV = 0 contra H1: CLV > 0. None sem erro padrão.

        `p < ALFA_CELULA` é IDÊNTICO a `significante` — é a mesma decisão escrita na
        escala que a correção de multiplicidade sabe comparar.
        """
        if self.erro_padrao is None or self.erro_padrao <= 0.0:
            return None
        z = self.media_cluster / self.erro_padrao
        return 0.5 * math.erfc(z / math.sqrt(2.0))


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


def veta_por_backtest(est: Estatistica, *, alfa: float = ALFA_CELULA) -> bool:
    """A célula é VETADA pelo histórico? (PC-VETO-BACKTEST, pré-registrado.)

    A regra é escrita ANTES de o backtest rodar sobre a base real — de propósito.
    Escolher o critério depois de ver os números é escolher o resultado, e o custo
    de um veto errado (perder uma célula boa para sempre) é grande demais para
    admitir essa liberdade.

    A ASSIMETRIA é o ponto (Sugestão nº 16 (c)):

      - **significativamente NEGATIVO → veta.** Há evidência histórica razoável de
        que a célula é ruim; não vale gastar temporada medindo o que já se sabe.
      - **inconclusivo → NÃO veta.** Segue em calibração. Ausência de prova não é
        prova de ausência (P6), e o backtest tem cobertura fina em OU/AH.
      - **positivo → não homologa**, só mantém a célula elegível para a sombra. O
        venue histórico não é o venue real (PC-VENUE-HISTORICO): o histórico pode
        MATAR uma hipótese, nunca aprová-la.

    Célula sem histórico algum — o Brasileirão, por exemplo — nunca é vetada: não
    há de onde tirar veto, e `veta_por_backtest` sequer é chamada para ela.

    Sem correção de multiplicidade aqui, e por escolha: no veto o erro custoso é o
    FALSO VETO (matar célula boa), e corrigir para múltiplos testes tornaria o veto
    ainda mais difícil de acionar — o que é o lado seguro. Manter o teste por célula
    é o mais RIGOROSO dos dois para quem veta, e é assim que fica.
    """
    if est.valor_p is None:
        return False
    # p unilateral acima; o lado negativo é o complementar.
    return (1.0 - est.valor_p) < alfa and est.media_cluster < 0.0


def homologaveis(
    celulas: dict[str, Estatistica], *, amostra_minima: int,
    alfa: float = ALFA_CELULA,
) -> set[str]:
    """Quais células do LOTE podem ser homologadas, corrigindo multiplicidade.

    O PROBLEMA que isto resolve, e que a auditoria não levantou: a granularidade
    escolhida (liga × mercado × linha × faixa de odd) cria centenas de células —
    6 ligas × 6 faixas × (1X2 + linhas de OU + linhas de AH) passa de 600. Testar
    cada uma a 95% e promover as que cruzam o limiar é dragagem de dados: com CLV
    verdadeiro ZERO em todas, ~17 células por rodada "provam" CLV positivo só por
    ruído. E cada promoção espúria autoriza dinheiro real (E7).

    Isto é consequência DIRETA da decisão de granularidade fina: quanto mais fina a
    célula, mais testes, mais falsos positivos. Quem escolhe a granularidade herda a
    correção — não dá para ficar com a precisão e não pagar por ela.

    Correção por **Benjamini–Hochberg** (FDR), não Bonferroni: a pergunta aqui é de
    TRIAGEM — entre muitas células, quais merecem operar? —, e o que se quer limitar
    é a proporção esperada de promoções falsas, não a probabilidade de um único erro
    em todo o lote. Bonferroni no tamanho desta família seria tão conservador que
    nenhuma célula real passaria.

    A FAMÍLIA é o lote avaliado na mesma rodada de promoção. Avaliar célula por
    célula em rodadas separadas para escapar da correção seria burlar o próprio
    critério — a multiplicidade existe no CONJUNTO de decisões, não no arquivo.

    Para UMA célula (m=1) o limiar volta a ser `alfa` e nada muda: é generalização
    estrita do critério da Sugestão nº 16, não um critério diferente.
    """
    # P12 primeiro: célula sem amostra não entra sequer na família — incluí-la
    # inflaria `m` e endureceria o limiar das demais por causa de quem nem podia
    # concorrer.
    elegiveis = {k: e for k, e in celulas.items()
                 if e.n >= amostra_minima and e.valor_p is not None}
    if not elegiveis:
        return set()
    ordenadas = sorted(elegiveis.items(), key=lambda kv: kv[1].valor_p)
    m = len(ordenadas)
    corte = 0
    for i, (_, e) in enumerate(ordenadas, start=1):
        if e.valor_p <= (i / m) * alfa:
            corte = i
    return {k for k, _ in ordenadas[:corte]}
