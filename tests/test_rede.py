"""Testes do detector de erro de rede (mensagem limpa nos CLIs)."""
import socket

from sinalizador.comum.rede import parece_erro_de_rede


def test_gaierror_direto():
    assert parece_erro_de_rede(socket.gaierror(11001, "getaddrinfo failed")) is True


def test_connectionerror_e_timeout():
    assert parece_erro_de_rede(ConnectionError("recusada")) is True
    assert parece_erro_de_rede(TimeoutError("estourou")) is True


def test_cadeia_de_causa():
    # simula httpx.ConnectError -> httpcore -> socket.gaierror
    base = socket.gaierror(11001, "getaddrinfo failed")
    meio = RuntimeError("httpcore ConnectError")
    meio.__cause__ = base
    topo = RuntimeError("falha ao inserir")
    topo.__cause__ = meio
    assert parece_erro_de_rede(topo) is True


def test_por_nome_da_classe():
    class ConnectError(Exception):
        pass

    assert parece_erro_de_rede(ConnectError("boom")) is True


def test_nao_e_erro_de_rede():
    assert parece_erro_de_rede(ValueError("outra coisa")) is False
    assert parece_erro_de_rede(KeyError("x")) is False
