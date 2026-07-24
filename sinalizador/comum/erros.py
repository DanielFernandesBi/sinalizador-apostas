"""Classificação de exceções do banco — SEM importar o SDK.

Mora aqui (e não em `comum/db.py`) de propósito: os NÚCLEOS (l1/orquestrador,
l4/clv) precisam reconhecer uma corrida de unicidade, mas não podem importar
`comum.db` — isso arrastaria o SDK do Supabase para dentro do núcleo e quebraria a
regra do projeto ("núcleo testável com fakes, sem SDK/rede"; o SDK só é amarrado
nos `cli.py`). Este módulo não importa nada.
"""
from __future__ import annotations

# Marcadores de violação de índice único. O SQLSTATE do Postgres é 23505; os demais
# existem porque o supabase-py embrulha o erro do PostgREST e nem sempre expõe o
# código em `.code` — sobra só o texto da mensagem.
_MARCAS_UNICIDADE = (
    "23505",
    "duplicate key",
    "unique constraint",
    "violates unique",
)


def e_violacao_unicidade(exc: BaseException) -> bool:
    """A exceção é uma violação de índice único do Postgres?

    É o backstop das corridas entre processos: quando dois daemons tentam gravar a
    MESMA linha lógica — o mesmo sinal aberto (`ux_sinais_candidato_aberto`), o mesmo
    CLV (`ux_clv_sinal` / `ux_clv_aborto`), o mesmo lançamento da banca
    (`ux_ledger_aposta_tipo`) — o banco recusa a segunda escrita. Isso NÃO é erro
    real: o registro já existe, que é exatamente o efeito desejado. Quem chama
    ignora e segue, em vez de derrubar o ciclo inteiro.

    Percorre a cadeia de causas (`__cause__`/`__context__`) porque o supabase-py
    frequentemente re-levanta o erro original embrulhado.
    """
    visto: set[int] = set()
    e: BaseException | None = exc
    while e is not None and id(e) not in visto:
        visto.add(id(e))
        txt = f"{getattr(e, 'code', '')} {e}".lower()
        if any(marca in txt for marca in _MARCAS_UNICIDADE):
            return True
        e = e.__cause__ or e.__context__
    return False
