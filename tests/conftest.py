import socket

import pytest

from iativa.synthetic_erp.generator import generate


@pytest.fixture(autouse=True)
def _sem_rede(monkeypatch):
    """Os testes não podem depender de rede: qualquer conexão de socket falha."""

    def bloqueado(*args, **kwargs):
        raise RuntimeError("Acesso à rede bloqueado nos testes automatizados.")

    monkeypatch.setattr(socket.socket, "connect", bloqueado)
    monkeypatch.setattr(socket.socket, "connect_ex", bloqueado)
    monkeypatch.setattr(socket, "create_connection", bloqueado)


@pytest.fixture
def usuario(django_user_model):
    return django_user_model.objects.create_user(username="usuario.teste", password="senha-de-teste-123")


@pytest.fixture
def cliente_logado(client, usuario):
    client.force_login(usuario)
    return client


@pytest.fixture
def erp_sintetico(tmp_path, settings):
    path = tmp_path / "erp_sintetico.sqlite3"
    generate(path)
    settings.SYNTHETIC_ERP_PATH = path
    return path
