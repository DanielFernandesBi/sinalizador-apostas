"""Testes do sync de governança (repo → config_sistema), com banco falso."""
from scripts.sync_governanca import sincronizar


class BancoFake:
    """Emula config_vigente/publicar_config sem rede."""

    def __init__(self, vigentes: dict[str, dict] | None = None):
        self._vig = dict(vigentes or {})
        self.publicados: list[tuple[str, str]] = []

    def config_vigente(self, chave):
        return self._vig.get(chave)

    def publicar_config(self, chave, valor):
        versao = (self._vig.get(chave, {}).get("versao", 0)) + 1
        novo = {"chave": chave, "versao": versao, "valor": valor, "vigente": True}
        self._vig[chave] = novo
        self.publicados.append((chave, valor))
        return novo


def _doc(tmp_path, nome, conteudo):
    p = tmp_path / nome
    p.write_text(conteudo, encoding="utf-8")
    return str(p)


def test_em_dia_nao_publica(tmp_path):
    caminho = _doc(tmp_path, "doutrina.md", "conteúdo idêntico\n")
    banco = BancoFake({"doutrina": {"versao": 3, "valor": "conteúdo idêntico\n", "vigente": True}})
    res = sincronizar(banco, {"doutrina": caminho})
    assert banco.publicados == []
    assert res[0]["acao"] == "em-dia"
    assert res[0]["versao_vigente"] == 3


def test_divergente_publica_verbatim_incrementando_versao(tmp_path):
    novo = "# Doutrina v0.1.1\ntexto do REPO verbatim\n"
    caminho = _doc(tmp_path, "doutrina.md", novo)
    banco = BancoFake({"doutrina": {"versao": 1, "valor": "texto CONDENSADO antigo", "vigente": True}})
    res = sincronizar(banco, {"doutrina": caminho})
    assert banco.publicados == [("doutrina", novo)]  # verbatim
    assert res[0]["acao"] == "publicado"
    assert res[0]["versao_vigente"] == 2


def test_sem_versao_no_banco_publica_v1(tmp_path):
    caminho = _doc(tmp_path, "manual.md", "primeiro conteúdo\n")
    banco = BancoFake({})
    res = sincronizar(banco, {"manual_crivo_l2": caminho})
    assert banco.publicados == [("manual_crivo_l2", "primeiro conteúdo\n")]
    assert res[0]["versao_vigente"] == 1


def test_dry_run_nao_escreve(tmp_path):
    caminho = _doc(tmp_path, "doutrina.md", "novo\n")
    banco = BancoFake({"doutrina": {"versao": 1, "valor": "velho", "vigente": True}})
    res = sincronizar(banco, {"doutrina": caminho}, dry_run=True)
    assert banco.publicados == []
    assert res[0]["acao"] == "divergente"
