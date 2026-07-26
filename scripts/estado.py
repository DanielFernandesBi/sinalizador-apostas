"""Gera a seção ESTADO do PLANO_MVP a partir do CHÃO DE VERDADE (auditoria item 7).

O PLANO envelhecia porque o estado era escrito à mão em prosa, em vários pontos, e
cada sessão atualizava alguns e esquecia outros: o cabeçalho ficou parado em
21/07/2026 com Doutrina v0.1.5 e "16 gates" enquanto o corpo já descrevia migrations
muito posteriores. A própria auditoria que apontou isso nasceu desatualizada — pediu
para "registrar 11 migrations" quando já eram vinte e uma.

É por isso que a correção NÃO é passar o documento a limpo. Um número escrito à mão
tem meia-vida de dias; o que muda a classe do problema é derivar o número da fonte e
**falhar a suíte** quando o documento discordar dela. Passar a limpo é o efeito, não
a causa.

O que é derivado (não se escreve à mão): migrations, gates, inventário de schema,
versões de governança, módulos e funções de teste. O que NÃO dá para derivar é a
MATURIDADE — "roda em produção com dado real" é fato sobre o mundo, não sobre o
repositório —, então ela é declarada aqui embaixo, com a regra de que subir de
degrau exige evidência datada.

Uso:
    python -m scripts.estado             # imprime
    python -m scripts.estado --escrever  # reescreve o bloco no PLANO_MVP.md
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
INICIO = "<!-- ESTADO:INICIO — gerado por `python -m scripts.estado --escrever`; não editar à mão -->"
FIM = "<!-- ESTADO:FIM -->"


# ---------------------------------------------------------------- chão de verdade

def migrations() -> list[str]:
    return sorted(p.name for p in (RAIZ / "db" / "migrations").glob("*.sql"))


def inventario_schema() -> dict[str, list[str]]:
    """Tabelas, views e funções DECLARADAS nas migrations.

    A fonte é o repo, não o banco: é o repo que precisa reproduzir o banco (a
    PC-MIGRATIONS-RECONCILIADAS existe porque um dia não reproduzia). Conferir
    contra o banco vivo é trabalho do advisor, não deste gerador — e um gerador
    que exige rede não roda na suíte.
    """
    tabelas: list[str] = []
    views: list[str] = []
    funcoes: list[str] = []
    for nome in migrations():
        sql = (RAIZ / "db" / "migrations" / nome).read_text(encoding="utf-8")
        tabelas += re.findall(r"^create table (?:if not exists )?(\w+)", sql, re.M)
        views += re.findall(r"^create (?:or replace )?view (\w+)", sql, re.M)
        funcoes += re.findall(r"^create (?:or replace )?function (\w+)", sql, re.M)
    # `create or replace` reaparece a cada alteração: conta objetos, não edições.
    return {"tabelas": sorted(set(tabelas)),
            "views": sorted(set(views)),
            "funcoes": sorted(set(funcoes))}


def gates() -> list[str]:
    from sinalizador.comum.gates import METADADOS_GATES
    return sorted(METADADOS_GATES)


def versao_documento(caminho: Path) -> str:
    """Maior versão declarada nas notas de rodapé do documento de governança."""
    txt = caminho.read_text(encoding="utf-8")
    versoes = re.findall(r"^\*v(\d+\.\d+\.\d+) —", txt, re.M)
    if not versoes:
        return "?"
    return "v" + max(versoes, key=lambda v: tuple(int(x) for x in v.split(".")))


def modulos() -> dict[str, list[str]]:
    saida: dict[str, list[str]] = {}
    for pacote in sorted((RAIZ / "sinalizador").iterdir()):
        if not pacote.is_dir() or pacote.name.startswith("_"):
            continue
        mods = sorted(p.stem for p in pacote.glob("*.py") if p.stem != "__init__")
        if mods:
            saida[pacote.name] = mods
    return saida


def funcoes_de_teste() -> int:
    """Funções `def test_` nos arquivos de teste.

    NÃO é o total que o pytest reporta — parametrização multiplica casos a partir de
    uma função. É contado assim de propósito: é determinístico e derivável sem rodar
    nada, então o número no documento não pode divergir do repo.
    """
    return sum(len(re.findall(r"^def test_", p.read_text(encoding="utf-8"), re.M))
               for p in sorted((RAIZ / "tests").glob("test_*.py")))


# ---------------------------------------------------------------- maturidade

# Os quatro degraus da auditoria (item 7). A distinção existe porque neste projeto
# ela é gritante: quase tudo está provado contra FAKE, e o degrau que autoriza
# confiar — dado real correndo no pipeline — ainda não foi alcançado por NADA.
#
#   implementado          o código existe
#   fake                  provado contra dublê/rollback, sem tocar o mundo
#   integracao            exercido contra o recurso REAL (banco vivo, API paga)
#   operacional           rodou em produção sobre dado real, com aceite declarado
#
# Subir de degrau exige evidência DATADA, escrita na coluna. "Parece que funciona"
# não é degrau. Rebaixar não precisa de cerimônia: na dúvida, desce.
MATURIDADE: tuple[tuple[str, str, str], ...] = (
    ("L0 captura (referência/varejo)", "integracao",
     "capturou 61 eventos reais da The Odds API; nenhum dentro do horizonte D+2"),
    ("L1 gatilhos e gates", "fake",
     "suíte contra dublês; nunca processou um ciclo com evento dentro do horizonte"),
    ("L2 crivo", "integracao",
     "`cli smoke` faz uma chamada real ao modelo contra o Manual vigente"),
    ("L3 notificação", "fake", "outbox e cartão provados contra bot dublê"),
    ("L4 fechamento e CLV", "fake", "nenhuma linha de fechamento real — sem jogo encerrado"),
    ("Schema e funções SQL", "integracao",
     "cada migration verificada no banco real em transação com rollback + controle negativo"),
    ("Vigia de daemons", "fake", "episódios provados contra dublê; nunca rodou como daemon"),
    ("Backtest (E6.1–E6.3)", "fake",
     "replay provado contra CSV sintético; não rodou sobre a base Football-Data real"),
    ("Homologação de mercados (E6.4)", "implementado",
     "critério e resolvedor prontos; NADA promove — sem dado, e o seed está todo em calibração"),
    ("Modo sombra ponta a ponta (E7)", "implementado",
     "nunca exercido: primeiro kickoff em 15/08/2026"),
)

_ROTULO = {"implementado": "implementado", "fake": "testado com fake",
           "integracao": "validado em integração", "operacional": "aceite operacional"}


# ---------------------------------------------------------------- render

def gerar() -> str:
    inv = inventario_schema()
    migs = migrations()
    gs = gates()
    linhas = [
        INICIO,
        "",
        "## ESTADO ATUAL",
        "",
        "**Este bloco é gerado.** Os números saem do repositório, não da memória de "
        "quem escreveu — e `tests/test_estado.py` falha se o documento divergir da "
        "fonte. Antes disso o estado era prosa escrita à mão em vários pontos, e o "
        "cabeçalho ficou dez versões de Doutrina atrasado sem que nada reclamasse.",
        "",
        "### Governança",
        "",
        f"- Doutrina (repo): **{versao_documento(RAIZ / 'docs' / 'doutrina_v0.1.md')}**",
        f"- Manual do Crivo L2 (repo): **{versao_documento(RAIZ / 'docs' / 'manual_crivo_L2_v0.1.md')}**",
        f"- Gates declarados em `gates.py`: **{len(gs)}**",
        "- A versão VIGENTE no banco é a do último `python -m scripts.sync_governanca`; "
        "divergir do repo é o normal entre o commit e o sync.",
        "",
        "### Schema",
        "",
        f"- Migrations no repo: **{len(migs)}** (`{migs[0]}` … `{migs[-1]}`)",
        f"- Tabelas: **{len(inv['tabelas'])}** · Views: **{len(inv['views'])}** · "
        f"Funções SQL: **{len(inv['funcoes'])}**",
        "",
        "### Código",
        "",
    ]
    for pacote, mods in modulos().items():
        linhas.append(f"- `{pacote}/`: {', '.join(f'`{m}`' for m in mods)}")
    linhas += [
        "",
        f"- Funções de teste: **{funcoes_de_teste()}** "
        "(o total do pytest é maior — parametrização multiplica casos)",
        "",
        "### Maturidade",
        "",
        "Quatro degraus, porque neste projeto a diferença é gritante: quase tudo está "
        "provado contra dublê, e **nada** alcançou aceite operacional — o primeiro "
        "kickoff é 15/08/2026, então não existe dado real para o pipeline processar. "
        "Subir de degrau exige evidência datada; descer não precisa de cerimônia.",
        "",
        "| componente | degrau | evidência |",
        "|---|---|---|",
    ]
    for componente, degrau, evidencia in MATURIDADE:
        linhas.append(f"| {componente} | **{_ROTULO[degrau]}** | {evidencia} |")
    n_op = sum(1 for _, d, _ in MATURIDADE if d == "operacional")
    linhas += [
        "",
        f"**Em aceite operacional: {n_op} de {len(MATURIDADE)}.** "
        "Enquanto esse número for zero, nenhuma conclusão sobre o comportamento do "
        "sistema em produção está disponível — nem boa nem ruim.",
        "",
        FIM,
    ]
    return "\n".join(linhas)


def escrever() -> bool:
    plano = RAIZ / "PLANO_MVP.md"
    txt = plano.read_text(encoding="utf-8")
    novo = gerar()
    if INICIO in txt and FIM in txt:
        i, j = txt.index(INICIO), txt.index(FIM) + len(FIM)
        atualizado = txt[:i] + novo + txt[j:]
    else:
        raise SystemExit(f"marcadores {INICIO!r} / {FIM!r} não encontrados no PLANO_MVP.md")
    if atualizado == txt:
        return False
    plano.write_text(atualizado, encoding="utf-8")
    return True


def main(argv: list[str]) -> int:
    if "--escrever" in argv:
        print("PLANO_MVP.md atualizado." if escrever() else "PLANO_MVP.md já estava em dia.")
    else:
        print(gerar())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
