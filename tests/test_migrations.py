"""Guarda de reconciliação repo × banco (achado #5 do 2º ciclo).

Sete gates dos ritos das Sugestões nº 3 e nº 5 foram semeados DIRETO no banco vivo e
nunca viraram migration: o banco tinha 16 gates e a 0001 semeava 9. Uma instalação
nova a partir do GitHub subiria com 9 — e como `gates.validar_integridade()` exige
TODOS os nomeados, nenhum daemon subiria. Repo e banco descreviam sistemas diferentes.

Estes testes são o tripwire que faltava: rodam sem banco e falham na hora em que
alguém acrescentar um gate ao código sem a migration correspondente (ou o contrário).
"""
from __future__ import annotations

import re
from pathlib import Path

from sinalizador.comum.gates import METADADOS_GATES

MIGRACOES = sorted((Path(__file__).resolve().parents[1] / "db" / "migrations").glob("*.sql"))


def _gates_semeados() -> set[str]:
    """Nomes de gate inseridos por QUALQUER migration do repo."""
    nomes: set[str] = set()
    for arquivo in MIGRACOES:
        sql = arquivo.read_text(encoding="utf-8")
        # cada bloco `insert into gates ... values ... ;`
        for bloco in re.findall(r"insert\s+into\s+gates\b.*?;", sql, re.S | re.I):
            nomes.update(re.findall(r"\(\s*'([a-z_]+)'\s*,", bloco))
    return nomes


def test_ha_migracoes():
    assert MIGRACOES, "nenhuma migration encontrada em db/migrations"


def test_todo_gate_do_codigo_tem_migration():
    """O tripwire do achado #5: gate exigido por `validar_integridade` sem seed em
    migration = instalação nova que não sobe."""
    faltando = set(METADADOS_GATES) - _gates_semeados()
    assert not faltando, (
        f"gates exigidos por gates.py sem seed em migration: {sorted(faltando)} — "
        f"uma instalação nova a partir do repo não subiria (validar_integridade falha)"
    )


def test_nenhum_gate_semeado_e_desconhecido_do_codigo():
    """O inverso: migration semeando gate que `METADADOS_GATES` não conhece — gate
    órfão, que ninguém valida e nenhuma camada lê."""
    orfaos = _gates_semeados() - set(METADADOS_GATES)
    assert not orfaos, f"migration semeia gate que gates.py desconhece: {sorted(orfaos)}"


def test_migracoes_numeradas_em_sequencia_sem_buracos():
    """Numeração contígua a partir de 0001: buraco costuma ser migration perdida no
    merge, e a ordem de aplicação é o que reproduz o banco."""
    numeros = [int(re.match(r"(\d+)", a.name).group(1)) for a in MIGRACOES]
    assert numeros == list(range(1, len(numeros) + 1)), (
        f"numeração com buraco ou fora de ordem: {numeros}"
    )


def test_migracao_inicial_e_imutavel_no_nome():
    """A 0001 é o alicerce: nunca se edita, sempre se acrescenta (regra do rito)."""
    assert MIGRACOES[0].name.startswith("0001_"), MIGRACOES[0].name
