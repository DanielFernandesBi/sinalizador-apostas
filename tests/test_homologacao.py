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


# ---- critério de homologação (Sugestão nº 16) ----

def test_homologavel_exige_p12_E_significancia():
    """Nem um critério nem o outro sozinho. P12 mede TAMANHO; o IC95 mede EVIDÊNCIA."""
    from sinalizador.comum.significancia import estatistica_agrupada, homologavel

    # n grande, média positiva, mas ruidosa → passa na P12 e reprova no IC95
    ruidosa = estatistica_agrupada({f"j{i}": [v] for i, v in
                                    enumerate([-30.0, 32.0] * 150)})
    assert ruidosa.n >= 200 and ruidosa.media > 0
    assert homologavel(ruidosa, amostra_minima=200) is False

    # IC95 impecável, mas amostra pequena → reprova na P12 (que é pétrea)
    pequena = estatistica_agrupada({f"j{i}": [1.0] for i in range(10)})
    assert pequena.significante is True
    assert homologavel(pequena, amostra_minima=200) is False

    # os dois → homologável
    boa = estatistica_agrupada({f"j{i}": [1.0 + (i % 3) * 0.1] for i in range(300)})
    assert homologavel(boa, amostra_minima=200) is True


def test_uma_definicao_de_significancia_para_os_dois_lados():
    """O backtest e o E6.4 precisam usar a MESMA conta — duas cópias divergem, e foi
    assim que `clv_pct` passou a significar fração de um lado e ponto percentual do
    outro."""
    import backtest.replay as replay
    from sinalizador.comum import significancia
    assert replay.estatistica_agrupada is significancia.estatistica_agrupada


# ---- multiplicidade (Sugestão nº 16 emendada) ----

def _celula_ruido(semente, n=200, media=0.0):
    """Célula com `n` jogos de 1 observação, ruído gaussiano de desvio 1."""
    import random
    r = random.Random(semente)
    return _est({f"g{i}": [r.gauss(media, 1.0)] for i in range(n)})


def _est(d):
    from sinalizador.comum.significancia import estatistica_agrupada
    return estatistica_agrupada(d)


def test_valor_p_e_a_mesma_decisao_que_o_ic95():
    """A correção fala em valor-p; o critério da Sugestão nº 16 fala em IC95. Se as
    duas escalas discordassem, a correção estaria mudando o critério por baixo."""
    from sinalizador.comum.significancia import ALFA_CELULA
    for semente in range(40):
        e = _celula_ruido(semente)
        assert e.significante == (e.valor_p < ALFA_CELULA)


def test_lote_grande_sem_clv_nenhum_nao_promove_nada():
    """684 células com CLV verdadeiro ZERO — a granularidade fina que escolhi cria
    esse tamanho de família. Sem correção, ~17 'provam' CLV positivo por ruído."""
    from sinalizador.comum.significancia import homologaveis
    celulas = {f"c{j}": _celula_ruido(1000 + j) for j in range(684)}
    sozinhas = {k for k, e in celulas.items() if e.significante}
    assert len(sozinhas) > 5, "o cenário precisa ter falsos positivos para valer"
    assert homologaveis(celulas, amostra_minima=200) == set()


def test_correcao_nao_mata_celula_verdadeiramente_boa():
    """Controle do controle: se a correção matasse tudo, seria só um 'não' caro."""
    from sinalizador.comum.significancia import homologaveis
    celulas = {f"c{j}": _celula_ruido(2000 + j) for j in range(100)}
    celulas["boa"] = _celula_ruido(999, n=400, media=0.6)   # CLV real e forte
    assert "boa" in homologaveis(celulas, amostra_minima=200)


def test_uma_celula_sozinha_mantem_o_criterio_original():
    """m=1 → limiar volta a ser alfa. A correção é generalização estrita, não um
    critério diferente: quem tem uma célula só não é punido por ela existir."""
    from sinalizador.comum.significancia import homologaveis
    boa = _celula_ruido(999, n=400, media=0.6)
    assert boa.significante is True
    assert homologaveis({"unica": boa}, amostra_minima=200) == {"unica"}


def test_celula_sem_p12_nao_entra_na_familia():
    """Incluir quem nem podia concorrer inflaria `m` e endureceria o limiar das
    demais — a correção puniria as boas por causa das inelegíveis."""
    from sinalizador.comum.significancia import homologaveis
    boa = _celula_ruido(999, n=400, media=0.6)
    curtas = {f"curta{j}": _celula_ruido(3000 + j, n=20) for j in range(300)}
    assert homologaveis({"boa": boa, **curtas}, amostra_minima=200) == {"boa"}


def test_um_cluster_so_nao_tem_valor_p():
    e = _est({"jogo unico": [1.0, 2.0, 3.0]})
    assert e.valor_p is None and e.significante is False
