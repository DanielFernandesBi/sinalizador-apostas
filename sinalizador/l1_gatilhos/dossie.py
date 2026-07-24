"""E2.7 — construtor do dossiê (Manual §1) + fila para o L2.

O dossiê é o JSON que o L1 entrega ao L2 (o crivo). Este módulo faz duas coisas:

  1. `construir_dossie(dados)` — valida e monta o `Dossie` (comum/modelos.py,
     transcrição do Manual §1). COMPLETO OU NADA: qualquer campo obrigatório
     ausente/malformado levanta `DossieIncompletoError` (P6 — abortar, nunca um
     dossiê parcial). Atende o aceite da E2: "nenhum sinal sem dossiê completo".

  2. `enfileirar_sinal(banco, dossie, ...)` — insere o sinal em `sinais` com
     `status = aguardando_crivo`. A tabela `sinais` COM esse status É a fila que
     o L2 consome (E3.1). A inserção é append-only via comum/db.py.

Nada aqui chama LLM (regra 2) nem move dinheiro (P1).
"""
from __future__ import annotations

from typing import Any, Optional, Protocol

from pydantic import ValidationError

from sinalizador.comum.modelos import Dossie


class DossieIncompletoError(ValueError):
    """Dossiê não pôde ser montado por dado ausente/malformado.

    Sinaliza P6 (abortar) na camada de montagem: nunca se emite um dossiê parcial.
    """


class _Inseridor(Protocol):
    def inserir(self, tabela: str, registro: dict[str, Any]) -> dict[str, Any]: ...


def construir_dossie(dados: dict[str, Any]) -> Dossie:
    """Valida `dados` (JSON do Manual §1) e devolve um `Dossie` completo.

    Levanta `DossieIncompletoError` se faltar/malformar qualquer campo obrigatório.
    """
    try:
        return Dossie.model_validate(dados)
    except ValidationError as e:
        raise DossieIncompletoError(
            f"dossiê incompleto/malformado: {e.error_count()} erro(s) de validação"
        ) from e


def enfileirar_sinal(
    banco: _Inseridor,
    dossie: Dossie,
    *,
    evento_id: str,
    casa_venue_id: str,
    linha: Optional[float] = None,
) -> dict[str, Any]:
    """Enfileira o sinal para o L2: INSERT em `sinais` (status aguardando_crivo).

    `evento_id` e `casa_venue_id` são as chaves de `eventos`/`casas` (vêm do L0).
    Os números denormalizados vêm do próprio dossiê; o dossiê completo vai em
    `sinais.dossie` (jsonb) para auditoria e para o L2.
    """
    m = dossie.matematica
    registro: dict[str, Any] = {
        # Identidade única (achado 3 da auditoria): a LINHA de `sinais` recebe o
        # MESMO UUID do dossiê. Sem isto o banco geraria um id próprio e o objeto
        # que o L2 analisa (dossie.sinal_id) ficaria desligado da linha que ele
        # transiciona (sinais.id) — quebra de rastreabilidade. O schema aceita id
        # explícito (o default gen_random_uuid() só age quando o id é omitido).
        "id": dossie.sinal_id,
        "evento_id": evento_id,
        "casa_venue_id": casa_venue_id,
        "gatilho": dossie.gatilho,
        "gatilho_anomalo": dossie.gatilho_anomalo,
        "caminho": dossie.caminho,
        "mercado": dossie.evento.mercado,
        "selecao": dossie.evento.selecao,
        "linha": linha,
        "p_justa": m.p_justa_shin,
        "odd_referencia": m.odd_referencia,
        "odd_venue": m.odd_venue,
        "edge_liquido_pct": m.edge_liquido * 100.0,      # dossiê em fração → coluna em %
        "stake_pct": m.stake_kelly_quarto * 100.0,       # idem
        "odd_minima_aceitavel": m.odd_minima_aceitavel,
        "dossie": dossie.model_dump(mode="json"),        # datetimes → ISO (jsonb)
        # status usa o default do schema: 'aguardando_crivo'
    }
    return banco.inserir("sinais", registro)
