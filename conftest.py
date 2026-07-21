"""Configuração de import do pytest.

Garante que o suite roda com `pytest` puro a partir da raiz do repositório (sem
exigir `PYTHONPATH=.`) e que os helpers de fixture em `tests/` (ex.: `_fixtura_odds`)
são importáveis por qualquer teste, independentemente do import-mode do pytest.
"""
import os
import sys

_RAIZ = os.path.dirname(os.path.abspath(__file__))
for _p in (_RAIZ, os.path.join(_RAIZ, "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
