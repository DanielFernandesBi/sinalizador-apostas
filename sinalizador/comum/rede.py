"""Detecção de erro de rede/DNS para os CLIs mostrarem mensagem limpa (em vez do
paredão de traceback do httpx/postgrest quando o Supabase não resolve/conecta)."""
from __future__ import annotations

import socket

MSG_REDE = (
    "Não consegui falar com o Supabase — é REDE/DNS, não o código.\n"
    "Cheque, nesta ordem:\n"
    "  1) internet ativa; `nslookup <projeto>.supabase.co` resolve;\n"
    "  2) proxy/VPN/antivírus com filtro de rede — desligue e teste;\n"
    "  3) se a rede usa proxy do sistema, o httpx (Supabase) NÃO lê o proxy do\n"
    "     Windows sozinho: defina HTTPS_PROXY no ambiente/.env;\n"
    "  4) teste de isolamento: rode pelo 4G (hotspot do celular)."
)


def parece_erro_de_rede(exc: BaseException) -> bool:
    """True se a exceção (ou alguma causa na cadeia) é falha de conexão/resolução
    de nome — ex.: Supabase inacessível por DNS/rede. Percorre __cause__/__context__."""
    visto: set[int] = set()
    e: BaseException | None = exc
    while e is not None and id(e) not in visto:
        visto.add(id(e))
        if isinstance(e, (socket.gaierror, ConnectionError, TimeoutError)):
            return True
        nome = type(e).__name__
        if "getaddrinfo" in str(e) or "ConnectError" in nome or "ConnectTimeout" in nome:
            return True
        e = e.__cause__ or e.__context__
    return False
