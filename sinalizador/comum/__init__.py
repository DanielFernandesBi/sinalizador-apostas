"""Comum — base compartilhada: config, cliente Supabase, modelos pydantic, log.

Toda escrita no banco passa por `comum/db.py` (service role, único ponto de
acesso). Contratos de dados (dossiê, saída L2, configs) são modelos pydantic.
Ver PLANO_MVP.md E0.3.
"""
