"""Chat mínimo: mensagem transitória, não persistida."""
import logging

import pytest
from django.conf import settings
from django.db import connection
from django.urls import reverse

pytestmark = pytest.mark.django_db(transaction=True)

MARCADOR = "MARCADOR-TRANSITORIO-7f3a quais clientes estão inadimplentes?"


def _todo_conteudo_do_banco() -> str:
    partes = []
    with connection.cursor() as cursor:
        for tabela in connection.introspection.table_names(cursor):
            cursor.execute(f'SELECT * FROM "{tabela}"')
            for linha in cursor.fetchall():
                partes.append(repr(linha))
    return "\n".join(partes)


def test_get_exibe_formulario(cliente_logado):
    resposta = cliente_logado.get(reverse("chat:index"))
    assert resposta.status_code == 200
    assert 'name="mensagem"' in resposta.content.decode()


def test_post_confirma_recebimento(cliente_logado):
    resposta = cliente_logado.post(reverse("chat:index"), {"mensagem": "Olá"})
    conteudo = resposta.content.decode()
    assert resposta.status_code == 200
    assert "recebida" in conteudo
    assert "Nada foi armazenado" in conteudo


def test_post_htmx_retorna_somente_o_painel(cliente_logado):
    resposta = cliente_logado.post(reverse("chat:index"), {"mensagem": "Olá"}, HTTP_HX_REQUEST="true")
    conteudo = resposta.content.decode()
    assert 'id="painel-chat"' in conteudo
    assert "<html" not in conteudo


def test_mensagem_vazia_ou_longa_e_rejeitada(cliente_logado):
    vazia = cliente_logado.post(reverse("chat:index"), {"mensagem": "   "})
    assert "recebida às" not in vazia.content.decode()
    longa = cliente_logado.post(reverse("chat:index"), {"mensagem": "x" * (settings.IATIVA_CHAT_MAX_CHARS + 1)})
    assert "recebida às" not in longa.content.decode()


def test_mensagem_nao_e_ecoada(cliente_logado):
    resposta = cliente_logado.post(reverse("chat:index"), {"mensagem": MARCADOR})
    assert MARCADOR not in resposta.content.decode()


def test_mensagem_nao_e_persistida_nem_registrada(cliente_logado, caplog):
    caplog.set_level(logging.DEBUG)
    cliente_logado.post(reverse("chat:index"), {"mensagem": MARCADOR})
    cliente_logado.post(reverse("chat:index"), {"mensagem": MARCADOR}, HTTP_HX_REQUEST="true")

    assert "MARCADOR-TRANSITORIO" not in _todo_conteudo_do_banco()
    assert "MARCADOR-TRANSITORIO" not in caplog.text
    sessao = cliente_logado.session
    assert all("MARCADOR-TRANSITORIO" not in repr(v) for v in sessao.values())
