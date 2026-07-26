"""Resolução da homologação por célula (Doutrina P2, migration 0020).

Espelho, no núcleo, de `fn_status_homologacao`. O que se trava aqui é a regra de
especificidade e — mais importante — que a faixa de odd NÃO se aplique antes de
existir preço: aplicá-la sem odd exigiria chutar em que faixa o candidato cairia,
que é fabricar o dado que decide (P6).
"""
import pytest

from sinalizador.l1_gatilhos.homologacao import Celula, TabelaHomologacao

LIGA = "Premier League"


def _tabela(*celulas):
    return TabelaHomologacao(celulas)


def test_celula_mais_especifica_vence_a_geral():
    t = _tabela(
        Celula(LIGA, "1x2", "backtest"),
        Celula(LIGA, "1x2", "homologado", odd_min=2.00, odd_max=2.60),
    )
    assert t.status(LIGA, "1x2", odd=2.10) == "homologado"
    assert t.status(LIGA, "1x2", odd=4.00) == "backtest"


def test_teto_da_faixa_e_exclusivo():
    """Mesma convenção de `backtest.FAIXAS`, para que [1.50, 2.00) e [2.00, 2.60)
    não se sobreponham em 2.00 — duas células cobrindo a mesma odd deixariam o
    desempate para a ordem de leitura."""
    t = _tabela(
        Celula(LIGA, "1x2", "backtest"),
        Celula(LIGA, "1x2", "homologado", odd_min=2.00, odd_max=2.60),
    )
    assert t.status(LIGA, "1x2", odd=2.00) == "homologado"   # piso inclusivo
    assert t.status(LIGA, "1x2", odd=2.60) == "backtest"     # teto exclusivo


def test_linha_limita_a_homologacao():
    """O backtest só mediu OU na 2.5. Homologar 'ou' sem linha autorizaria a 3.5,
    sobre a qual não existe evidência nenhuma."""
    t = _tabela(Celula(LIGA, "ou", "homologado", linha=2.5))
    assert t.status(LIGA, "ou", linha=2.5, odd=1.90) == "homologado"
    assert t.status(LIGA, "ou", linha=3.5, odd=1.90) is None


def test_ausencia_e_none_nunca_licenca():
    """None = falha de configuração. P2 não autoriza calibração implícita."""
    t = _tabela(Celula(LIGA, "1x2", "homologado"))
    assert t.status("Serie A", "1x2", odd=2.10) is None
    assert t.status(LIGA, "ah", odd=2.10) is None


def test_celula_de_faixa_nao_se_aplica_sem_odd():
    """Antes do line shopping não há preço. Uma célula com limite de odd não pode
    ser resolvida aí — e o fallback correto é NÃO cobrir, não adivinhar a faixa."""
    t = _tabela(Celula(LIGA, "1x2", "homologado", odd_min=2.00, odd_max=2.60))
    assert t.status(LIGA, "1x2", odd=None) is None
    # ...mas o GRUPO ainda sabe que existe caminho, sem precisar da odd:
    assert t.status_do_grupo(LIGA, "1x2") == "operavel"


def test_grupo_operavel_nao_promete_homologacao():
    """'operavel' só diz que há caminho. Um mercado com a faixa alta homologada e a
    baixa suspensa passa no grupo — é no candidato que a faixa dele é julgada."""
    t = _tabela(
        Celula(LIGA, "1x2", "homologado", odd_min=2.00, odd_max=2.60),
        Celula(LIGA, "1x2", "suspenso", odd_min=1.01, odd_max=2.00),
    )
    assert t.status_do_grupo(LIGA, "1x2") == "operavel"
    assert t.status(LIGA, "1x2", odd=2.10) == "homologado"
    assert t.status(LIGA, "1x2", odd=1.50) == "suspenso"


def test_grupo_com_tudo_retirado_devolve_o_terminal():
    t = _tabela(Celula(LIGA, "1x2", "suspenso"), Celula(LIGA, "1x2", "caducado", linha=1.0))
    assert t.status_do_grupo(LIGA, "1x2") in ("suspenso", "caducado")
    assert t.status_do_grupo("Serie A", "1x2") is None


def test_de_mapa_antigo_significa_todos_os_limites_nulos():
    t = TabelaHomologacao.de({(LIGA, "1x2"): "homologado"})
    assert t.status(LIGA, "1x2", odd=1.05) == "homologado"
    assert t.status(LIGA, "1x2", linha=2.5, odd=9.99) == "homologado"


def test_de_linhas_le_suspenso_em_como_suspenso():
    t = TabelaHomologacao.de([
        {"liga": LIGA, "mercado": "1x2", "status": "homologado",
         "linha": None, "odd_min": None, "odd_max": None,
         "suspenso_em": "2026-07-01T00:00:00Z"},
    ])
    assert t.status(LIGA, "1x2", odd=2.0) == "suspenso"


@pytest.mark.parametrize("odd,esperado", [
    (1.49, "faixa_baixa"), (1.50, "faixa_media"), (1.99, "faixa_media"), (2.00, None),
])
def test_faixas_adjacentes_nao_se_sobrepoem_nem_deixam_buraco(odd, esperado):
    t = _tabela(
        Celula(LIGA, "1x2", "faixa_baixa", odd_min=1.01, odd_max=1.50),
        Celula(LIGA, "1x2", "faixa_media", odd_min=1.50, odd_max=2.00),
    )
    assert t.status(LIGA, "1x2", odd=odd) == esperado
