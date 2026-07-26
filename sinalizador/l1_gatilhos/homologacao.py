"""Resolução da homologação por CÉLULA (Doutrina P2) — determinística, sem IA.

Espelha `fn_status_homologacao` (migration 0020) do lado do núcleo, para que o L1
decida sem ida ao banco por candidato. A regra é uma só: vence a célula MAIS
ESPECÍFICA que cobre (liga, mercado, linha, odd).

Por que a célula precisa de faixa de odd: o backtest conclui por liga × mercado ×
linha × faixa de odd, e a homologação só sabia dizer liga × mercado. Se uma faixa
tem CLV positivo e outra negativo, homologar o mercado inteiro autoriza a faixa ruim
junto com a boa — a autorização afirmaria mais do que a evidência sustenta. E quem
não quisesse isso teria de não homologar nada, deixando a evidência boa sem uso.

PONTO ESTRUTURAL que a auditoria não nomeia: **a faixa é propriedade do PREÇO, e o
preço é por seleção.** O gate de homologação era uma decisão de GRUPO (liga,
mercado) tomada antes de existir preço algum. Com faixa, ele não cabe mais inteiro
ali: parte continua no grupo (o que é decidível sem preço — existe alguma célula
para este mercado?) e parte desce para o candidato, depois do line shopping, quando
a odd finalmente existe. Ignorar isso e resolver a faixa no grupo obrigaria a
inventar uma odd representativa — fabricar o dado que decide (P6).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional

# Status que autorizam ALGUMA coisa: 'homologado' opera; 'backtest'/'calibracao'
# geram candidato_sombra (só CLV). O resto é bloqueio.
STATUS_OPERAVEIS = ("homologado", "backtest", "calibracao")
STATUS_TERMINAIS = ("suspenso", "caducado")


@dataclass(frozen=True)
class Celula:
    liga: str
    mercado: str
    status: str
    linha: Optional[float] = None
    odd_min: Optional[float] = None
    odd_max: Optional[float] = None      # EXCLUSIVO (ver 0020)

    @property
    def especificidade(self) -> int:
        return sum(x is not None for x in (self.linha, self.odd_min, self.odd_max))

    def cobre(self, *, linha: Optional[float], odd: Optional[float]) -> bool:
        if self.linha is not None and (linha is None or float(linha) != float(self.linha)):
            return False
        # Limite de odd declarado + odd desconhecida = NÃO cobre. Uma célula de faixa
        # não pode ser aplicada antes de existir preço: seria decidir pela faixa sem
        # saber em qual faixa se está.
        if self.odd_min is not None and (odd is None or float(odd) < float(self.odd_min)):
            return False
        if self.odd_max is not None and (odd is None or float(odd) >= float(self.odd_max)):
            return False
        return True


class TabelaHomologacao:
    """As células vigentes, com a resolução do status por candidato."""

    def __init__(self, celulas: Iterable[Celula]) -> None:
        self._celulas = list(celulas)

    def __len__(self) -> int:
        return len(self._celulas)

    @classmethod
    def de(cls, dados: Any) -> "TabelaHomologacao":
        """Aceita a lista de linhas de `mercados_homologados` OU o mapa antigo.

        O mapa `{(liga, mercado): status}` não é um formato paralelo: é EXATAMENTE o
        caso de célula com todos os limites nulos ("qualquer linha, qualquer odd").
        Lê-lo assim mantém o significado, não o traduz.
        """
        if not dados:
            return cls(())
        if isinstance(dados, dict):
            return cls(Celula(liga=l, mercado=m, status=s)
                       for (l, m), s in dados.items())
        celulas = []
        for r in dados:
            liga, mercado = r.get("liga"), r.get("mercado")
            if not liga or not mercado:
                continue
            status = "suspenso" if r.get("suspenso_em") else (r.get("status") or "")
            celulas.append(Celula(
                liga=liga, mercado=mercado, status=status,
                linha=None if r.get("linha") is None else float(r["linha"]),
                odd_min=None if r.get("odd_min") is None else float(r["odd_min"]),
                odd_max=None if r.get("odd_max") is None else float(r["odd_max"]),
            ))
        return cls(celulas)

    def status(self, liga: str, mercado: str, *, linha: Optional[float] = None,
               odd: Optional[float] = None) -> Optional[str]:
        """Status da célula MAIS ESPECÍFICA que cobre. None = nenhuma cobre.

        None é falha de configuração, não licença: quem chama trata como fail-closed
        (P2 não autoriza calibração implícita).
        """
        candidatas = [c for c in self._celulas
                      if c.liga == liga and c.mercado == mercado
                      and c.cobre(linha=linha, odd=odd)]
        if not candidatas:
            return None
        return max(candidatas, key=lambda c: c.especificidade).status

    def status_do_grupo(self, liga: str, mercado: str,
                        linha: Optional[float] = None) -> Optional[str]:
        """Decisão de GRUPO, tomada ANTES de existir preço.

        Só responde o que é decidível sem odd: há alguma célula para este mercado?
        Alguma delas ainda opera? Devolve:
          - None                      → nada configurado (fail-loud, pula o grupo);
          - 'suspenso'/'caducado'     → TUDO que existe está retirado (pula o grupo);
          - 'operavel'                → há caminho; a decisão fina é por candidato.

        Note que 'operavel' NÃO promete homologação: um mercado com a faixa alta
        homologada e a baixa suspensa passa por aqui, e é no candidato que a faixa
        dele é julgada. Antecipar essa decisão aqui só seria possível chutando a odd.
        """
        do_mercado = [c for c in self._celulas
                      if c.liga == liga and c.mercado == mercado
                      and (c.linha is None or (linha is not None
                                               and float(linha) == float(c.linha)))]
        if not do_mercado:
            return None
        if any(c.status in STATUS_OPERAVEIS for c in do_mercado):
            return "operavel"
        # Só terminais: devolve o mais específico para o log dizer QUAL retirada foi.
        return max(do_mercado, key=lambda c: c.especificidade).status
