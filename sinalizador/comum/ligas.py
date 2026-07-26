"""Contrato de LIGA — a mesma liga tem que se chamar a mesma coisa nos dois lados.

Este módulo existe porque não existia. O backtest rotulava `"Inglaterra — Premier
League"` (de `football_data.LIGAS`) e a produção grava `"Premier League"` em
`eventos.liga` (de `mapeamento.SPORTS_ALVO`). São duas tabelas de tradução
independentes para o MESMO conceito, e o comentário do `mapeamento` chegava a
afirmar que estava "alinhado a football_data.LIGAS" — afirmação falsa que tornava
o desalinhamento invisível na leitura.

O efeito não é estético. A homologação (`mercados_homologados`) é chaveada por
`eventos.liga`, e a evidência que autoriza homologar vem do backtest. Com rótulos
diferentes, **nenhuma célula do backtest casa com nenhuma liga de produção**: a
prova existe, a autorização existe, e as duas nunca se encontram. O gate é
fail-closed (sem linha, sem sinal), então isso não abre risco — produz MUDEZ, que
é o modo de falha que este sistema menos consegue perceber.

O `rotulo` é o que já está gravado nos eventos e continua sendo a chave de
homologação: mudá-lo exigiria reescrever `eventos.liga`, e não há razão para pagar
esse preço — o problema nunca foi o valor, foi haver dois donos dele. `chave` é o
identificador estável (imune a acento, travessão e capitalização) para quando algo
precisar de uma chave que não seja texto de exibição.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Liga:
    chave: str          # identificador estável (slug) — nunca exibido ao usuário
    rotulo: str         # o que vai em `eventos.liga` e na chave de homologação
    sport_key: str      # The Odds API (produção)
    div: Optional[str]  # divisão do Football-Data (backtest); None = sem histórico


LIGAS: tuple[Liga, ...] = (
    Liga("eng_premier_league", "Premier League", "soccer_epl", "E0"),
    Liga("esp_la_liga", "La Liga", "soccer_spain_la_liga", "SP1"),
    Liga("ita_serie_a", "Serie A", "soccer_italy_serie_a", "I1"),
    Liga("ger_bundesliga", "Bundesliga", "soccer_germany_bundesliga", "D1"),
    Liga("fra_ligue_1", "Ligue 1", "soccer_france_ligue_one", "F1"),
    Liga("por_primeira_liga", "Primeira Liga", "soccer_portugal_primeira_liga", "P1"),
    # BRASILEIRÃO SÉRIE A — entra com `div=None`: o Football-Data NÃO cobre o
    # Brasileirão, então esta liga NUNCA terá backtest histórico. Isso é permitido
    # pela Sugestão nº 16 (c) — ausência de backtest não é veto; backtest só VETA, e
    # quem homologa é o CLV de sombra, medido no venue real. Foi incluída para o
    # sistema parar de esperar: as seis europeias só começam em 15/08, e o
    # Brasileirão está em temporada AGORA. Cada dia sem captura é amostra que não
    # se recupera. Série B fica de fora de propósito — não se amplia liga e mercado
    # ao mesmo tempo (D6).
    Liga("bra_serie_a", "Brasileirão Série A", "soccer_brazil_campeonato", None),
)

# Derivações — cada lado consome a SUA vista da mesma tabela, e nenhum lado
# mantém a própria cópia.
POR_SPORT_KEY: dict[str, str] = {l.sport_key: l.rotulo for l in LIGAS}
POR_DIV: dict[str, str] = {l.div: l.rotulo for l in LIGAS if l.div}
POR_ROTULO: dict[str, Liga] = {l.rotulo: l for l in LIGAS}

# Ligas de interesse SEM histórico no Football-Data — o backtest não pode dizer
# nada sobre elas, e por isso elas não podem ser homologadas por backtest (D6).
SEM_HISTORICO: tuple[str, ...] = tuple(l.rotulo for l in LIGAS if not l.div)
