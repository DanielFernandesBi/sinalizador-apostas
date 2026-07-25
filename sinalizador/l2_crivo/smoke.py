"""Smoke test do caminho profundo contra a API REAL (P1.7).

Os testes do L2 usam fake: verificam a NOSSA lógica, não as nossas suposições sobre
a API. Este comando verifica as suposições — e é a única coisa aqui que gasta
crédito. Ele responde, com uma chamada de verdade:

  1. o modelo configurado existe e responde;
  2. o `type` da ferramenta de busca ainda é aceito (versões de ferramenta mudam);
  3. `thinking adaptive` + `output_config.effort` são aceitos juntos nesse modelo;
  4. o caminho profundo produz JSON válido pelo contrato do Manual §8;
  5. quanto custou de fato — tokens, cache, buscas, continuações de `pause_turn`.

Não toca o banco, não cria sinal, não altera estado nenhum: monta um dossiê
sintético mínimo e imprime o que voltou. Rodar depois de trocar de modelo, de
mexer no Manual, ou quando o custo do caminho profundo surpreender.

    python -m sinalizador.l2_crivo.cli smoke
    python -m sinalizador.l2_crivo.cli smoke --caminho rapido   # sem busca web
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from .modelo import (
    ModeloAnthropic,
    RecusaDoModeloError,
    RespostaIncompletaError,
    RespostaModelo,
)

# Dossiê sintético: forma mínima que o Manual espera, valores obviamente fictícios.
# Não representa aposta nenhuma e nunca chega ao banco.
DOSSIE_SINTETICO: dict[str, Any] = {
    "gatilho": "value_bet",
    "gatilho_anomalo": False,
    "caminho": "profundo",
    "banca_origem": "papel",
    "evento": {
        "liga": "Premier League", "partida": "Time A x Time B",
        "data_hora_utc": "2026-08-21T19:00:00Z", "mercado": "1x2", "selecao": "1",
    },
    "matematica": {
        "p_justa_shin": 0.52, "odd_referencia": 1.95, "odd_venue": 2.10,
        "edge_liquido": 0.03, "stake_kelly_quarto": 0.008,
        "odd_minima_aceitavel": 2.04, "comissao_aplicada": 0.0,
    },
    "snapshots": {
        "ts_fonte_referencia": "2026-08-21T18:30:00Z",
        "ts_fonte_venue": "2026-08-21T18:30:10Z",
        "janela_sincronia_ok": True, "referencia_estavel_ok": True,
        "historico_movimento_1h": {"referencia": [], "venue": []},
    },
    "liquidez": {"disponivel_no_preco": 0.0, "profundidade_book": None,
                 "liquidez_aplicavel": False, "gate_liquidez_ok": None},
    "venues_comparados": [],
    "exposicao": {"por_jogo": 0.0, "por_liga_dia": 0.0, "por_dia": 0.0,
                  "gates_exposicao_ok": True, "stake_valor": 8.0, "banca_valor": 1000.0},
    "tipster": None,
}


def rodar_smoke(api_key: str, manual: str, *, caminho: str = "profundo",
                modelo_id: str | None = None) -> int:
    """Uma chamada real. Devolve 0 se a resposta CONCLUIU e trouxe JSON."""
    dossie = {**DOSSIE_SINTETICO, "sinal_id": str(uuid.uuid4()), "caminho": caminho}
    modelo = ModeloAnthropic(api_key, **({"modelo": modelo_id} if modelo_id else {}))

    print(f"[smoke] caminho={caminho} modelo={modelo_id or 'padrão'} — chamando a API…")
    try:
        resp: RespostaModelo = modelo.avaliar(
            system=manual,
            dossie_json=json.dumps(dossie, ensure_ascii=False),
            caminho=caminho,
        )
    except RespostaIncompletaError as e:
        # NÃO é falha do smoke test: é exatamente o desfecho que o P1.7 tornou
        # visível. Antes isso chegava como texto cortado e virava `erro` permanente.
        print(f"[smoke] resposta INCOMPLETA: {e.motivo}")
        print("[smoke] o crivo trataria como 'adiado' (candidato preservado) — ok")
        return 2
    except RecusaDoModeloError as e:
        print(f"[smoke] modelo RECUSOU (categoria={e.categoria})")
        print("[smoke] o crivo trataria como 'erro' permanente — retentar não muda")
        return 3

    print(f"[smoke] stop_reason={resp.stop_reason} latencia={resp.latencia_ms}ms")
    print(f"[smoke] tokens: entrada={resp.tokens_entrada} saida={resp.tokens_saida} "
          f"cache_leitura={resp.tokens_cache_leitura} cache_escrita={resp.tokens_cache_escrita}")
    print(f"[smoke] buscas_web={resp.buscas_web} continuacoes={resp.continuacoes}")
    print(f"[smoke] custo_usd (SEM a cobrança por uso da busca)={resp.custo_usd}")

    from .crivo import SaidaInvalidaError, extrair_json, validar_saida
    try:
        saida = validar_saida(extrair_json(resp.texto), sinal_id_esperado=dossie["sinal_id"])
    except SaidaInvalidaError as e:
        print(f"[smoke] SAÍDA INVÁLIDA pelo contrato do Manual §8: {e}")
        print(f"[smoke] texto recebido (300 primeiros): {resp.texto[:300]!r}")
        return 1
    print(f"[smoke] saída válida — verdict={saida.verdict} "
          f"caminho_executado={saida.caminho_executado} "
          f"fatores={len(saida.fatores)} fontes={len(saida.fontes_consultadas)}")
    return 0
