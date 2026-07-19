"""Sinalizador de Apostas — pacote raiz.

Sistema que EXCLUSIVAMENTE notifica (Doutrina P1–P12). Nenhum módulo executa
aposta, movimenta dinheiro ou acessa conta em modo de escrita.

Pipeline: L0 captura → L1 gatilhos (mecânico) → L2 crivo (IA) → L3 notifica
→ L4 fechamento/CLV. Ver PLANO_MVP.md e docs/doutrina_v0.1.md.
"""
