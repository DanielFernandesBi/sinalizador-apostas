"""O PLANO não pode divergir do repositório (auditoria item 7).

Sem isto, a correção do item 7 seria só passar o documento a limpo — e um número
escrito à mão tem meia-vida de dias. A prova está na própria auditoria: ela pediu
para "registrar 11 migrations" quando já eram vinte e uma. O que muda a classe do
problema não é o documento certo hoje, é a suíte FALHAR quando ele ficar errado.
"""
from pathlib import Path

import pytest

from scripts import estado

PLANO = Path(__file__).resolve().parent.parent / "PLANO_MVP.md"


def test_bloco_de_estado_esta_em_dia_com_o_repo():
    """Se isto falhar, rode `python -m scripts.estado --escrever` e confira o diff.

    Falhar aqui NÃO é chateação de formatação: significa que o documento que toda
    sessão lê como fonte da verdade está afirmando coisa que o repo desmente.
    """
    txt = PLANO.read_text(encoding="utf-8")
    assert estado.INICIO in txt and estado.FIM in txt, "marcadores do bloco sumiram"
    atual = txt[txt.index(estado.INICIO):txt.index(estado.FIM) + len(estado.FIM)]
    assert atual == estado.gerar(), (
        "PLANO_MVP.md divergiu do repositório — rode `python -m scripts.estado --escrever`")


def test_gerador_le_a_fonte_e_nao_o_documento():
    """O gerador tem que derivar de arquivo, não copiar o que o PLANO já dizia —
    senão ele carimbaria o erro em vez de corrigi-lo."""
    assert len(estado.migrations()) == len(
        list((PLANO.parent / "db" / "migrations").glob("*.sql")))
    from sinalizador.comum.gates import METADADOS_GATES
    assert estado.gates() == sorted(METADADOS_GATES)


def test_versao_de_governanca_e_a_MAIOR_nao_a_ultima_escrita():
    """As notas de versão da Doutrina são listadas em ordem decrescente; pegar a
    primeira ou a última linha daria a versão errada dependendo da ordem de escrita.
    Ordena-se numericamente."""
    doutrina = PLANO.parent / "docs" / "doutrina_v0.1.md"
    v = estado.versao_documento(doutrina)
    assert v.startswith("v0.1.")
    partes = [int(x) for x in v[1:].split(".")]
    for linha in doutrina.read_text(encoding="utf-8").splitlines():
        if linha.startswith("*v0.1."):
            outra = [int(x) for x in linha[2:].split(" ")[0].split(".")]
            assert outra <= partes, f"{linha[:20]} é maior que a versão reportada {v}"


def test_maturidade_declara_evidencia_para_cada_componente():
    """Degrau sem evidência é opinião. A regra é que subir exige dizer POR QUÊ."""
    for componente, degrau, evidencia in estado.MATURIDADE:
        assert degrau in estado._ROTULO, f"{componente}: degrau desconhecido {degrau!r}"
        assert len(evidencia) > 20, f"{componente}: evidência vazia ou vaga"


def test_nada_esta_em_aceite_operacional():
    """Trava deliberada, e a mais importante deste arquivo.

    Nenhum componente pode ser declarado `operacional` enquanto o pipeline não tiver
    processado dado real — o primeiro kickoff é 15/08/2026. Quando isso mudar, este
    teste falha e obriga quem for promover a APAGAR a trava conscientemente, em vez
    de deixar um degrau subir sozinho num commit de rotina.
    """
    operacionais = [c for c, d, _ in estado.MATURIDADE if d == "operacional"]
    assert operacionais == [], (
        f"{operacionais} foram declarados em aceite operacional — se for verdade, "
        "remova esta trava explicitamente e registre a evidência datada no PLANO")


def test_metodos_do_fechamento_antigo_nao_voltam():
    """`marcar_evento_encerrado` escrevia `eventos.status='encerrado'` e
    `eventos_iniciados_sem_status_final` LIA esse estado. Desde a migration 0007 o
    marcador do L4 é `clv_eventos_finalizados`: ninguém escreve mais, e o leitor,
    se voltasse, devolveria TODO evento iniciado para sempre. Não é código morto —
    é armadilha carregada, e por isso tem tripwire."""
    from sinalizador.comum.db import Banco
    for morto in ("marcar_evento_encerrado", "eventos_iniciados_sem_status_final",
                  "clv_ids_registrados"):
        assert not hasattr(Banco, morto), (
            f"{morto} voltou à fachada; o L4 usa clv_resultados/clv_eventos_finalizados")


@pytest.mark.parametrize("obsoleto", ["16 tabelas", "6 views, triggers", "16 gates vigentes"])
def test_numeros_antigos_nao_sobrevivem_no_estado(obsoleto):
    """Os números exatos que a auditoria pegou desatualizados não podem reaparecer
    dentro do bloco gerado. (Fora dele, no HISTÓRICO, são registro do que se sabia
    na época — ali é legítimo.)"""
    txt = PLANO.read_text(encoding="utf-8")
    bloco = txt[txt.index(estado.INICIO):txt.index(estado.FIM)]
    assert obsoleto not in bloco
