"""Contabilidade da banca: contrato, atomicidade e idempotência (achado #1, 2º ciclo).

O `ClienteFake` abaixo SIMULA a semântica das funções SQL da migration 0004
(`fn_registrar_aposta` / `fn_liquidar_aposta`): transação tudo-ou-nada, índice único
`(aposta_id, tipo)` e liquidação única. Ele NÃO é o Postgres — a fidelidade da função
SQL real é verificada contra o banco (ver o cabeçalho da 0004); aqui se trava o
CONTRATO que a camada `db.py` precisa cumprir e o comportamento que a CLI depende.
"""
from decimal import Decimal

import pytest

from sinalizador.comum.db import Banco
from sinalizador.comum.dinheiro import ValorInvalidoError, payout_bruto


class ErroBanco(RuntimeError):
    """Equivale ao erro que a função SQL levanta (e que desfaz a transação)."""


class _Resp:
    def __init__(self, data):
        self.data = data


class ClienteFake:
    """Simula o Postgres: RPC transacional + índice único do ledger."""

    def __init__(self, falhar_no_ledger=False):
        self.apostas = {}
        self.ledger = []
        self._seq = 0
        self._falhar_no_ledger = falhar_no_ledger   # simula falha ENTRE as escritas

    # -- infra --
    def _saldo(self):
        return self.ledger[-1]["saldo_apos"] if self.ledger else Decimal("0")

    def _lancar(self, tipo, valor, aposta_id, saldo_apos):
        # índice único parcial ux_ledger_aposta_tipo (aposta_id, tipo)
        if any(l["aposta_id"] == aposta_id and l["tipo"] == tipo for l in self.ledger):
            raise ErroBanco(f"duplicate key value violates unique constraint "
                            f"ux_ledger_aposta_tipo ({aposta_id}, {tipo})")
        if self._falhar_no_ledger:
            raise ErroBanco("falha simulada no INSERT do ledger")
        self.ledger.append({"tipo": tipo, "valor": valor, "aposta_id": aposta_id,
                            "saldo_apos": saldo_apos})

    def rpc(self, funcao, params):
        self._pendente = (funcao, params)
        return self

    def execute(self):
        funcao, p = self._pendente
        if funcao == "fn_registrar_aposta":
            return _Resp(self._registrar(p))
        if funcao == "fn_liquidar_aposta":
            return _Resp(self._liquidar(p))
        raise AssertionError(f"RPC inesperada: {funcao}")

    # -- fn_registrar_aposta --
    def _registrar(self, p):
        stake, odd = Decimal(p["p_stake"]), Decimal(p["p_odd"])
        if stake <= 0:
            raise ErroBanco("stake deve ser > 0")
        if odd <= 1:
            raise ErroBanco("odd deve ser > 1")
        self._seq += 1
        ap_id = f"ap-{self._seq}"
        # snapshot para o rollback: a transação é tudo-ou-nada
        antes_apostas, antes_ledger = dict(self.apostas), list(self.ledger)
        try:
            self.apostas[ap_id] = {"id": ap_id, "sinal_id": p["p_sinal_id"],
                                   "casa_id": p["p_casa_id"], "odd_executada": odd,
                                   "stake_valor": stake, "resultado": "pendente"}
            saldo = self._saldo() - stake
            self._lancar("aposta", -stake, ap_id, saldo)
        except ErroBanco:
            self.apostas, self.ledger = antes_apostas, antes_ledger   # ROLLBACK
            raise
        return {"aposta": dict(self.apostas[ap_id]), "saldo": saldo}

    # -- fn_liquidar_aposta --
    def _liquidar(self, p):
        ap = self.apostas.get(p["p_aposta_id"])
        if ap is None:
            raise ErroBanco(f"aposta {p['p_aposta_id']} não encontrada")
        if ap["resultado"] != "pendente":
            raise ErroBanco(f"aposta {ap['id']} já liquidada — a liquidação é única")
        bruto = payout_bruto(p["p_resultado"], ap["stake_valor"], ap["odd_executada"])
        lucro = bruto - ap["stake_valor"]
        antes_apostas = {k: dict(v) for k, v in self.apostas.items()}
        antes_ledger = list(self.ledger)
        try:
            ap["resultado"] = p["p_resultado"]
            ap["payout_bruto"], ap["lucro_liquido"] = bruto, lucro
            saldo = self._saldo() + bruto
            self._lancar("liquidacao", bruto, ap["id"], saldo)
        except ErroBanco:
            self.apostas, self.ledger = antes_apostas, antes_ledger   # ROLLBACK
            raise
        return {"aposta_id": ap["id"], "resultado": p["p_resultado"],
                "payout_bruto": bruto, "lucro_liquido": lucro, "saldo": saldo}


def _banco(**kw):
    cli = ClienteFake(**kw)
    return Banco(client=cli), cli


def _ciclo(banco, resultado, stake="20", odd="2.05"):
    r = banco.registrar_aposta(sinal_id="s-1", casa_id="c-1",
                               odd_executada=odd, stake_valor=stake)
    return banco.liquidar_e_lancar(aposta_id=r["aposta"]["id"], resultado=resultado)


# ---- os cinco resultados: variação da banca ponta a ponta ----

@pytest.mark.parametrize("resultado,variacao", [
    ("green",      Decimal("21.00")),
    ("red",        Decimal("-20.00")),
    ("void",       Decimal("0.00")),
    ("meio_green", Decimal("10.50")),
    ("meio_red",   Decimal("-10.00")),
])
def test_variacao_da_banca_ponta_a_ponta(resultado, variacao):
    banco, cli = _banco()
    r = _ciclo(banco, resultado)
    assert cli._saldo() == variacao          # débito do stake + crédito do bruto
    assert r["lucro_liquido"] == variacao


def test_green_credita_bruto_e_nao_o_lucro():
    # Regressão do bug: a banca subia +1 (creditava o lucro 21 sobre o débito -20).
    banco, cli = _banco()
    r = _ciclo(banco, "green")
    assert r["payout_bruto"] == Decimal("41.00")
    assert cli._saldo() == Decimal("21.00")
    assert [l["valor"] for l in cli.ledger] == [Decimal("-20"), Decimal("41.00")]


# ---- idempotência e atomicidade ----

def test_segunda_liquidacao_falha_sem_novo_lancamento():
    banco, cli = _banco()
    r = banco.registrar_aposta(sinal_id="s-1", casa_id="c-1",
                               odd_executada="2.05", stake_valor="20")
    ap_id = r["aposta"]["id"]
    banco.liquidar_e_lancar(aposta_id=ap_id, resultado="green")
    saldo_apos_primeira, n = cli._saldo(), len(cli.ledger)

    with pytest.raises(ErroBanco, match="única"):
        banco.liquidar_e_lancar(aposta_id=ap_id, resultado="green")

    assert cli._saldo() == saldo_apos_primeira and len(cli.ledger) == n


def test_aposta_nao_recebe_dois_debitos():
    # índice único (aposta_id, tipo): o 2º débito da MESMA aposta é recusado.
    banco, cli = _banco()
    banco.registrar_aposta(sinal_id="s-1", casa_id="c-1",
                           odd_executada="2.05", stake_valor="20")
    with pytest.raises(ErroBanco, match="ux_ledger_aposta_tipo"):
        cli._lancar("aposta", Decimal("-20"), "ap-1", Decimal("-40"))
    assert len([l for l in cli.ledger if l["tipo"] == "aposta"]) == 1


def test_falha_no_ledger_desfaz_a_aposta_inteira():
    # A falha parcial que corrompia a banca: aposta gravada, débito não. Agora a
    # transação é uma só — se o ledger falha, a aposta não fica registrada.
    banco, cli = _banco(falhar_no_ledger=True)
    with pytest.raises(ErroBanco):
        banco.registrar_aposta(sinal_id="s-1", casa_id="c-1",
                               odd_executada="2.05", stake_valor="20")
    assert cli.apostas == {} and cli.ledger == []


def test_liquidacoes_concorrentes_nao_geram_dois_creditos():
    # Duas liquidações da mesma aposta (corrida): a 2ª encontra resultado ≠ pendente.
    banco, cli = _banco()
    r = banco.registrar_aposta(sinal_id="s-1", casa_id="c-1",
                               odd_executada="2.05", stake_valor="20")
    ap_id = r["aposta"]["id"]
    ok = 0
    for _ in range(2):
        try:
            banco.liquidar_e_lancar(aposta_id=ap_id, resultado="green")
            ok += 1
        except ErroBanco:
            pass
    assert ok == 1
    assert len([l for l in cli.ledger if l["tipo"] == "liquidacao"]) == 1


def test_saldo_bate_com_a_soma_integral_do_ledger():
    banco, cli = _banco()
    for resultado in ("green", "red", "void", "meio_green", "meio_red"):
        _ciclo(banco, resultado)
    assert cli._saldo() == sum(l["valor"] for l in cli.ledger)
    # 21 − 20 + 0 + 10,50 − 10 = 1,50
    assert cli._saldo() == Decimal("1.50")


# ---- validação antes da rede ----

def test_stake_e_odd_invalidos_sao_recusados_antes_do_banco():
    banco, cli = _banco()
    with pytest.raises(ValorInvalidoError):
        banco.registrar_aposta(sinal_id="s-1", casa_id="c-1",
                               odd_executada="2.05", stake_valor="0")
    with pytest.raises(ValorInvalidoError):
        banco.registrar_aposta(sinal_id="s-1", casa_id="c-1",
                               odd_executada="1", stake_valor="20")
    assert cli.apostas == {} and cli.ledger == []


def test_resultado_invalido_e_recusado_antes_do_banco():
    banco, _ = _banco()
    with pytest.raises(ValueError):
        banco.liquidar_e_lancar(aposta_id="ap-1", resultado="ganhou")


def test_liquidar_nao_aceita_valor_digitado():
    # O contrato novo NÃO tem parâmetro de retorno — o payout é derivado no banco.
    banco, _ = _banco()
    with pytest.raises(TypeError):
        banco.liquidar_e_lancar(aposta_id="ap-1", resultado="green", retorno_liquido=21)
