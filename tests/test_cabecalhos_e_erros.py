"""Cabeçalhos anti-armazenamento, CSP e páginas de erro sem detalhes técnicos."""
import pytest
from django.test import Client
from django.urls import reverse

pytestmark = pytest.mark.django_db


def _assert_no_store(resposta):
    assert "no-store" in resposta["Cache-Control"]
    assert "script-src 'self'" in resposta["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in resposta["Content-Security-Policy"]
    assert resposta["X-Frame-Options"] == "DENY"
    assert resposta["X-Content-Type-Options"] == "nosniff"


def test_no_store_no_login(client):
    _assert_no_store(client.get(reverse("login")))


def test_no_store_no_chat(cliente_logado):
    _assert_no_store(cliente_logado.get(reverse("chat:index")))
    _assert_no_store(cliente_logado.post(reverse("chat:index"), {"mensagem": "olá"}))


def test_404_generico(cliente_logado):
    resposta = cliente_logado.get("/nao-existe/")
    assert resposta.status_code == 404
    _assert_no_store(resposta)
    assert "Página não encontrada" in resposta.content.decode()


@pytest.mark.urls("tests.urls_erro")
def test_500_sem_pagina_tecnica(usuario):
    client = Client(raise_request_exception=False)
    client.force_login(usuario)
    resposta = client.get("/erro/")
    assert resposta.status_code == 500
    conteudo = resposta.content.decode()
    assert "Traceback" not in conteudo
    assert "VALOR_SECRETO_LOCAL" not in conteudo
    assert "Nenhum detalhe técnico" in conteudo
