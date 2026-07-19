"""Sincroniza os documentos de governança do repo → `config_sistema` (E0.1/rito).

O REPO é a fonte da verdade. O fluxo é SEMPRE repo → banco, e só após rito
aprovado (ver Doutrina §7). Este script NUNCA edita arquivos — ele apenas lê
`docs/` e, para cada documento, compara o conteúdo do arquivo com a versão
VIGENTE no banco:

  - iguais byte a byte  → em dia, nada a fazer;
  - diferentes (ou sem versão no banco) → publica uma NOVA versão verbatim
    (`Banco.publicar_config`), preservando o histórico.

Assim o system prompt do L2 (E3.1, lido de `config_sistema.manual_crivo_l2`)
espelha o repo exatamente, e a paridade se mantém a cada rito sem recopiar à mão.

Uso (com o `.env` presente — service role):
    python -m scripts.sync_governanca            # aplica
    python -m scripts.sync_governanca --dry-run  # só mostra o que faria
"""
from __future__ import annotations

import argparse
from pathlib import Path

# chave em config_sistema  ->  caminho do documento no repo
DOCS: dict[str, str] = {
    "doutrina": "docs/doutrina_v0.1.md",
    "manual_crivo_l2": "docs/manual_crivo_L2_v0.1.md",
}


def sincronizar(banco, docs: dict[str, str], *, dry_run: bool = False) -> list[dict]:
    """Compara cada doc com a versão vigente e publica se divergir.

    `docs` mapeia chave → caminho de arquivo. `banco` expõe `config_vigente` e
    `publicar_config` (ver comum/db.py). Retorna um resumo por chave.
    """
    resultados: list[dict] = []
    for chave, caminho in docs.items():
        conteudo = Path(caminho).read_text(encoding="utf-8")
        vigente = banco.config_vigente(chave)
        if vigente is not None and vigente.get("valor") == conteudo:
            resultados.append({"chave": chave, "acao": "em-dia",
                               "versao_vigente": vigente.get("versao")})
            continue
        acao = "divergente" if dry_run else "publicado"
        if not dry_run:
            novo = banco.publicar_config(chave, conteudo)
            resultados.append({"chave": chave, "acao": acao,
                               "versao_vigente": novo.get("versao")})
        else:
            resultados.append({"chave": chave, "acao": acao,
                               "versao_vigente": (vigente or {}).get("versao")})
    return resultados


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Sincroniza governança (repo → config_sistema)")
    ap.add_argument("--raiz", default=".", help="raiz do repo (default: cwd)")
    ap.add_argument("--dry-run", action="store_true", help="não escreve; só reporta")
    args = ap.parse_args(argv)

    # Import tardio: só precisa do .env/supabase quando de fato roda.
    from sinalizador.comum.db import Banco

    docs = {chave: str(Path(args.raiz) / rel) for chave, rel in DOCS.items()}
    banco = Banco()
    resultados = sincronizar(banco, docs, dry_run=args.dry_run)

    for r in resultados:
        print(f"{r['chave']:16} {r['acao']:11} versao={r['versao_vigente']}")
    mudou = any(r["acao"] in ("publicado", "divergente") for r in resultados)
    if args.dry_run and mudou:
        print("\n(dry-run) há divergência — rode sem --dry-run para publicar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
