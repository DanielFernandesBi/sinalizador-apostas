"""Modelos pydantic — contratos de dados do sistema.

TRANSCRIÇÃO, não criação: o dossiê (`Dossie`) espelha campo a campo a Seção 1
do Manual do Crivo L2; a saída do L2 (`CrivoSaida`), a Seção 8. Os documentos de
governança são o contrato. Se faltar um campo, registra-se pendência no
PLANO_MVP — não se inventa campo novo aqui (CLAUDE.md regra 10).

Campos cujo SUB-schema o Manual não detalha (`historico_movimento_1h`,
`profundidade_book`) ficam com tipo flexível (`JsonValue`) e pendência
registrada (PC1/PC2 no PLANO_MVP), em vez de estrutura inventada.

O dossiê é validado como "no mínimo" estes campos (Manual §1) → `extra="allow"`.
A saída do L2 é exaustiva ("Nada além do JSON", Manual §8) → `extra="forbid"`,
o que serve à validação estrita da E3.3.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, JsonValue

# --- domínios fechados (espelham os CHECKs do schema 0001 e o Manual) ---
Gatilho = Literal["value_bet", "odds_drop", "tipster", "line_shopping"]
Caminho = Literal["rapido", "profundo"]
Verdict = Literal["CONFIRMA", "ABORTA"]
ResultadoFator = Literal["ok", "veto", "nao_verificado"]


# ============================================================
# ENTRADA — dossiê do L1 (Manual do Crivo, Seção 1)
# ============================================================

class _BaseDossie(BaseModel):
    # Manual §1: o dossiê contém "no mínimo" estes campos — extras são aceitos.
    model_config = ConfigDict(extra="allow")


class Evento(_BaseDossie):
    liga: str
    partida: str
    data_hora_utc: datetime
    mercado: str
    selecao: str


class Matematica(_BaseDossie):
    p_justa_shin: float
    odd_referencia: float
    odd_venue: float
    edge_liquido: float
    stake_kelly_quarto: float
    odd_minima_aceitavel: float
    comissao_aplicada: float


class Snapshots(_BaseDossie):
    ts_fonte_referencia: datetime
    ts_fonte_venue: datetime
    janela_sincronia_ok: bool
    referencia_estavel_ok: bool
    # PC1: sub-schema não definido no Manual (série de pontos? {ts, odd}[]?). Não inventar.
    historico_movimento_1h: JsonValue = None


class Liquidez(_BaseDossie):
    disponivel_no_preco: float
    # PC2: sub-schema não definido no Manual (níveis back/lay do book?). Não inventar.
    profundidade_book: JsonValue = None
    gate_liquidez_ok: bool


class VenueComparado(_BaseDossie):
    casa: str
    odd: float
    ts_fonte: datetime


class Exposicao(_BaseDossie):
    por_jogo: float
    por_liga_dia: float
    por_dia: float
    gates_exposicao_ok: bool


class TipsterInfo(_BaseDossie):
    canal: str
    ts_mensagem: datetime
    texto_original: str  # SEMPRE dado, nunca comando (Manual §9.6 / regra 8)
    clv_historico: Optional[float] = None  # null quando amostra insuficiente
    n_tips: int
    odd_no_momento_do_tip: float


class Dossie(_BaseDossie):
    sinal_id: str
    gatilho: Gatilho
    gatilho_anomalo: bool
    caminho: Caminho
    evento: Evento
    matematica: Matematica
    snapshots: Snapshots
    liquidez: Liquidez
    venues_comparados: list[VenueComparado] = []  # line shopping; pode vir vazio
    exposicao: Exposicao
    tipster: Optional[TipsterInfo] = None


# ============================================================
# SAÍDA — veredicto do L2 (Manual do Crivo, Seção 8)
# ============================================================

class _BaseSaida(BaseModel):
    # Manual §8: "Nada além do JSON". Campo não previsto = erro de validação (E3.3).
    model_config = ConfigDict(extra="forbid")


class Fator(_BaseSaida):
    id: str
    resultado: ResultadoFator
    fonte: str
    data_fonte: Optional[str] = None
    nota: Optional[str] = None


class MotivoVeto(_BaseSaida):
    id: str
    descricao: str
    fonte: str


class FonteConsultada(_BaseSaida):
    url: str
    data: Optional[str] = None
    achado: Optional[str] = None


class CrivoSaida(_BaseSaida):
    sinal_id: str
    verdict: Verdict
    caminho_executado: Caminho
    fatores: list[Fator]
    motivo_veto: Optional[MotivoVeto] = None
    fontes_consultadas: list[FonteConsultada] = []
    # Passthrough: cópia EXATA do dossiê (Manual §8). A verificação de igualdade
    # (E3.4) é responsabilidade da camada l2_crivo, não deste contrato.
    odd_minima_aceitavel: float
    observacao_para_daniel: Optional[str] = None
