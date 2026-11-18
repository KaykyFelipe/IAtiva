"""M71: excluir conversa (só o dono, só individual)."""
import pytest
from django.urls import reverse

from iativa.chat import conversas
from iativa.chat.models import ConversationContext

from .test_ciclo_agente import base  # noqa: F401

pytestmark = pytest.mark.django_db


def test_dono_exclui_a_propria_conversa(base, client):
    c = conversas.nova_individual(base['ana'])
    outra = conversas.nova_individual(base['caio'])
    client.force_login(base['ana'])
    assert client.post(reverse('chat:excluir', args=[outra.pk])).status_code == 404
    assert client.get(reverse('chat:excluir', args=[c.pk])).status_code == 302  # M87: GET volta à conversa
    assert client.post(reverse('chat:excluir', args=[c.pk])).status_code == 204
    assert not ConversationContext.objects.filter(pk=c.pk).exists()
    assert ConversationContext.objects.filter(pk=outra.pk).exists()


def test_lateral_tem_lixeira_e_conversa_parcial_por_htmx(base, client):
    c = conversas.nova_individual(base['ana'])
    c.titulo = 'Teste'; c.save()
    client.force_login(base['ana'])
    assert 'data-excluir' in open('templates/chat/_lateral.html', encoding='utf-8').read()
    parcial = client.get(reverse('chat:conversa', args=[c.pk]), HTTP_HX_REQUEST='true').content.decode()
    assert 'id="conversa"' in parcial and '<html' not in parcial


def test_endereco_de_envio_aberto_no_navegador_volta_para_a_conversa(base, client):
    """M87: depois do login, voltar para /c/<id>/mensagem/ (só envio) leva à conversa, não ao erro 405."""
    c = conversas.nova_individual(base['ana'])
    client.force_login(base['ana'])
    r = client.get(reverse('chat:mensagem', args=[c.pk]))
    assert r.status_code == 302 and r.url == reverse('chat:conversa', args=[c.pk])
    assert client.get(reverse('chat:nova')).url == reverse('chat:index')


def test_login_sempre_cai_na_pagina_principal_e_conversa_excluida_volta_ao_inicio(base, client):
    """M88: o "next" do login é ignorado; conversa que não existe mais leva à página principal."""
    from django.contrib.auth.models import User  # noqa: F401
    u = base['ana']
    u.set_password('senha-de-teste-123'); u.save()
    r = client.post(reverse('login') + '?next=/c/999/mensagem/', {'username': u.username, 'password': 'senha-de-teste-123'})
    assert r.status_code == 302 and r.url == reverse('chat:index')
    c = conversas.nova_individual(u)
    conversas.excluir(u, c.pk)
    r = client.get(reverse('chat:conversa', args=[c.pk]))
    assert r.status_code == 302 and r.url == reverse('chat:index')
