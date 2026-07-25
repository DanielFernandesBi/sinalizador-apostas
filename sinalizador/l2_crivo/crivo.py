"""E3.3/E3.4/E3.5 — o crivo L2: valida a saída, confere passthrough, grava e decide.

Fluxo por sinal (fila = `sinais` com status `aguardando_crivo`):
  1. system prompt = Manual do Crivo VIGENTE (`config_sistema`, nunca hard-coded);
  2. chama o modelo (rápido/profundo conforme `dossie.caminho`);
  3. VALIDAÇÃO ESTRITA da saída (pydantic `CrivoSaida`, extra=forbid — Manual §8):
     JSON inválido / fora do schema / veredicto fora do domínio → `sinais.status = erro`
     + notificação administrativa, **NUNCA aprovação por default** (E3.3);
  4. verificação de assimetria (E3.4): `odd_minima_aceitavel` do crivo ≡ a do dossiê
     (passthrough) — qualquer divergência = erro;
  5. grava `crivos` (latência, tokens, custo) e transiciona `sinais.status`
     (CONFIRMA→confirmado, ABORTA→vetado).

Invariante inviolável: **falha jamais vira CONFIRMA.** Qualquer exceção no caminho
leva a `erro`, nunca a `confirmado`.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from pydantic import ValidationError

from sinalizador.comum.erros import e_falha_transitoria
from sinalizador.comum.modelos import CrivoSaida

from .modelo import ModeloCrivo, RespostaModelo

_log = logging.getLogger(__name__)

DAEMON = "l2"
_TOL_ODD = 1e-6  # tolerância do passthrough numérico da odd mínima


class SaidaInvalidaError(ValueError):
    """Saída do modelo não é o JSON estrito do Manual §8 (ou fere o passthrough)."""


def carregar_manual(banco: Any) -> str:
    """System prompt = Manual do Crivo vigente da `config_sistema` (nunca hard-coded)."""
    doc = banco.config_vigente("manual_crivo_l2")
    if not doc or not doc.get("valor"):
        raise SaidaInvalidaError("Manual do Crivo (manual_crivo_l2) ausente na config_sistema — L2 não sobe (P6)")
    return doc["valor"]


def extrair_json(texto: str) -> dict[str, Any]:
    """Extrai o objeto JSON da resposta (tolera cerca ```json). Falha alto se não houver."""
    t = (texto or "").strip()
    if "```" in t:  # remove cercas de código, se houver
        partes = t.split("```")
        for p in partes:
            p = p.strip()
            if p.startswith("json"):
                p = p[4:].strip()
            if p.startswith("{"):
                t = p
                break
    ini, fim = t.find("{"), t.rfind("}")
    if ini < 0 or fim <= ini:
        raise SaidaInvalidaError("resposta do crivo não contém objeto JSON")
    try:
        return json.loads(t[ini : fim + 1])
    except json.JSONDecodeError as e:
        raise SaidaInvalidaError(f"JSON do crivo malformado: {e}") from e


def validar_saida(dados: dict[str, Any], *, sinal_id_esperado: str) -> CrivoSaida:
    """Valida contra `CrivoSaida` (extra=forbid) e confere o id do sinal."""
    try:
        saida = CrivoSaida.model_validate(dados)
    except ValidationError as e:
        raise SaidaInvalidaError(f"saída do crivo fora do schema (Manual §8): {e.error_count()} erro(s)") from e
    if saida.sinal_id != sinal_id_esperado:
        raise SaidaInvalidaError(
            f"sinal_id da saída ({saida.sinal_id!r}) ≠ do dossiê ({sinal_id_esperado!r})"
        )
    return saida


def verificar_passthrough(saida: CrivoSaida, dossie: dict[str, Any]) -> None:
    """E3.4 — a `odd_minima_aceitavel` do crivo tem de ser cópia EXATA da do dossiê."""
    do_dossie = float(dossie["matematica"]["odd_minima_aceitavel"])
    if abs(saida.odd_minima_aceitavel - do_dossie) > _TOL_ODD:
        raise SaidaInvalidaError(
            f"passthrough violado: odd_minima do crivo ({saida.odd_minima_aceitavel}) "
            f"≠ do dossiê ({do_dossie})"
        )


@dataclass
class ResumoCrivo:
    avaliados: int = 0
    confirmados: int = 0
    vetados: int = 0
    erros: int = 0
    adiados: int = 0        # falha transitória: segue na fila, NÃO vira erro (P0.2)
    perdidos_kickoff: int = 0   # a partida começou durante a chamada ao modelo


def _registrar_erro(banco: Any, sinal_id: str, motivo: str) -> None:
    """status = erro + alerta administrativo. NUNCA vira CONFIRMA."""
    try:
        banco.transicionar_status_sinal(sinal_id, "erro")
    except Exception:  # já pode não estar em aguardando_crivo — o alerta ainda vale
        _log.exception("falha ao marcar sinal como erro", extra={"sinal_id": sinal_id})
    banco.inserir("notificacoes", {
        "sinal_id": sinal_id, "tipo": "administrativo", "canal": "telegram",
        "conteudo": f"[crivo:erro] sinal {sinal_id}: {motivo}",
        # sem status: o default do schema e 'pendente' (a outbox do L3 entrega).
    })
    _log.error("crivo falhou — sinal marcado erro", extra={"sinal_id": sinal_id, "motivo": motivo})


def avaliar_sinal(banco: Any, modelo: ModeloCrivo, sinal: dict[str, Any], *, manual: str) -> str:
    """Avalia um sinal. Devolve o status final ('confirmado'|'vetado'|'erro')."""
    sinal_id = sinal["id"]
    dossie = sinal.get("dossie") or {}
    # Identidade única (achado 3 da auditoria): a linha e o dossiê que o modelo
    # analisa têm de ser o MESMO objeto. O L1 insere a linha com id = dossie.sinal_id;
    # se aqui divergirem, esta linha carrega o dossiê de OUTRA — erro, jamais CONFIRMA.
    if dossie.get("sinal_id") != sinal_id:
        _registrar_erro(banco, sinal_id,
                        f"identidade quebrada: sinais.id ({sinal_id!r}) ≠ "
                        f"dossie.sinal_id ({dossie.get('sinal_id')!r})")
        return "erro"
    caminho = dossie.get("caminho", "rapido")
    try:
        resp: RespostaModelo = modelo.avaliar(
            system=manual, dossie_json=json.dumps(dossie, ensure_ascii=False, default=str),
            caminho=caminho,
        )
        dados = extrair_json(resp.texto)
        # Valida a saída contra o ID REAL da linha (== dossie.sinal_id, garantido acima).
        saida = validar_saida(dados, sinal_id_esperado=sinal_id)
        verificar_passthrough(saida, dossie)
    except SaidaInvalidaError as e:
        # PERMANENTE: repetir não conserta (JSON inválido, schema, passthrough).
        _registrar_erro(banco, sinal_id, str(e))
        return "erro"
    except Exception as e:
        if e_falha_transitoria(e):
            # TRANSITÓRIA (rede, 429, 5xx): NÃO vira desfecho. Com a unicidade por
            # aposta lógica (Sugestão nº 11), marcar `erro` mataria o candidato para
            # sempre por causa de uma indisponibilidade momentânea. Fica na fila; o
            # retry é limitado pelo kickoff, quando o L4 fecha como timeout_crivo.
            _log.warning("falha transitória no crivo — sinal segue na fila",
                         extra={"sinal_id": sinal_id, "erro": f"{type(e).__name__}: {e}"})
            return "adiado"
        _registrar_erro(banco, sinal_id, f"exceção inesperada: {e}")
        return "erro"

    # Saída válida → grava crivo E transiciona numa ÚNICA transação (P0.2). Antes
    # eram duas operações soltas: entre elas o L4 podia rodar `fn_timeout_crivo`,
    # deixando parecer gravado com sinal em timeout, ou status mudado após o apito.
    novo_status = "confirmado" if saida.verdict == "CONFIRMA" else "vetado"
    r = banco.concluir_crivo(sinal_id, {
        "verdict": saida.verdict,
        "caminho_executado": saida.caminho_executado,
        "fatores": [f.model_dump(mode="json") for f in saida.fatores],
        "motivo_veto": saida.motivo_veto.model_dump(mode="json") if saida.motivo_veto else None,
        "fontes_consultadas": [c.model_dump(mode="json") for c in saida.fontes_consultadas],
        "observacao": saida.observacao_para_daniel,
        "modelo": resp.modelo,
        "latencia_ms": resp.latencia_ms,
        "tokens_entrada": resp.tokens_entrada,
        "tokens_saida": resp.tokens_saida,
        "custo_usd": resp.custo_usd,
    }, novo_status)

    if not r.get("aplicado"):
        motivo = r.get("motivo")
        _log.warning("crivo não aplicado", extra={"sinal_id": sinal_id, "motivo": motivo,
                                                  "status": r.get("status")})
        # `kickoff_ultrapassado`: a partida começou DURANTE a chamada ao modelo. O
        # veredicto perdeu validade e o banco já fechou o sinal como timeout_crivo,
        # sem gravar parecer — registrar opinião sobre aposta inexistente sujaria a
        # auditoria do crivo (vw_clv_por_veto).
        return str(r.get("status") or "erro")

    _log.info("crivo concluído", extra={"sinal_id": sinal_id, "verdict": saida.verdict,
                                        "status": novo_status, "custo_usd": resp.custo_usd})
    return novo_status


def processar_fila(banco: Any, modelo: ModeloCrivo, *, limite: int = 50,
                   agora_iso: Optional[str] = None) -> ResumoCrivo:
    """Processa a fila do L2 (sinais aguardando crivo). Pulsa o heartbeat `l2`.

    Com `agora_iso`, a fila exclui sinais de partidas JÁ INICIADAS (Sugestão nº 10):
    o crivo não confirma aposta depois do apito — esses viram `timeout_crivo` no L4.
    """
    manual = carregar_manual(banco)  # falha alto se o Manual não estiver vigente
    resumo = ResumoCrivo()
    for sinal in banco.sinais_aguardando_crivo(limite, agora_iso):
        status = avaliar_sinal(banco, modelo, sinal, manual=manual)
        resumo.avaliados += 1
        if status == "confirmado":
            resumo.confirmados += 1
        elif status == "vetado":
            resumo.vetados += 1
        elif status == "adiado":
            resumo.adiados += 1
        elif status == "timeout_crivo":
            resumo.perdidos_kickoff += 1
        else:
            resumo.erros += 1
    banco.pulsar(DAEMON, {"avaliados": resumo.avaliados, "confirmados": resumo.confirmados,
                          "vetados": resumo.vetados, "erros": resumo.erros,
                          "adiados": resumo.adiados,
                          "perdidos_kickoff": resumo.perdidos_kickoff})
    _log.info("ciclo L2 concluído", extra={"avaliados": resumo.avaliados,
                                           "confirmados": resumo.confirmados,
                                           "vetados": resumo.vetados, "erros": resumo.erros})
    return resumo
