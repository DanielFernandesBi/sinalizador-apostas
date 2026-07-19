"""L2 — Crivo IA: cliente Anthropic + validação estrita da saída.

Único módulo autorizado a chamar LLM. System prompt = config_sistema.manual_crivo_l2
vigente (nunca hard-coded). Falha jamais vira aprovação (regra 4);
`odd_minima_aceitavel` é passthrough verificado (regra 3). Ver PLANO_MVP.md E3.
"""
