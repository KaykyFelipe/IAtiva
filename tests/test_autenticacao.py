"""Autenticação inicial, CSRF e cookies."""
import pytest
from django.test import Client
from django.urls import reverse

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("nome", ["chat:index"])
def test_paginas_exigem_login(client, nome):
    resposta = client.get(reverse(nome))
    assert resposta.status_code == 302
    assert resposta["Location"].startswith(reverse("login"))


def test_admin_exige_login(client):
    resposta = client.get("/admin/")
    assert resposta.status_code == 302


def test_pagina_de_login_e_publica(client):
    assert client.get(reverse("login")).status_code == 200


def test_post_do_chat_sem_login_e_redirecionado(client):
    resposta = client.post(reverse("chat:index"), {"mensagem": "teste"})
    assert resposta.status_code == 302


def test_login_e_logout(client, usuario):
    resposta = client.post(reverse("login"), {"username": "usuario.teste", "password": "senha-de-teste-123"})
    assert resposta.status_code == 302
    assert resposta["Location"] == reverse("chat:index")
    assert client.get(reverse("chat:index")).status_code == 200

    cookie = client.cookies["iativa_sessao"]
    assert cookie["httponly"]
    assert cookie["samesite"] == "Strict"

    assert client.post(reverse("logout")).status_code == 302
    assert client.get(reverse("chat:index")).status_code == 302


def test_logout_por_get_nao_e_aceito(cliente_logado):
    assert cliente_logado.get(reverse("logout")).status_code == 405


def test_login_com_senha_errada(client, usuario):
    resposta = client.post(reverse("login"), {"username": "usuario.teste", "password": "errada"})
    assert resposta.status_code == 200
    assert "inválidos" in resposta.content.decode()
    assert client.get(reverse("chat:index")).status_code == 302


def test_csrf_obrigatorio(usuario):
    client = Client(enforce_csrf_checks=True)
    client.force_login(usuario)
    resposta = client.post(reverse("chat:index"), {"mensagem": "sem token"})
    assert resposta.status_code == 403
    assert "Traceback" not in resposta.content.decode()


def test_cookie_csrf_httponly(client):
    client.get(reverse("login"))
    cookie = client.cookies["iativa_csrf"]
    assert cookie["httponly"]
    assert cookie["samesite"] == "Strict"
