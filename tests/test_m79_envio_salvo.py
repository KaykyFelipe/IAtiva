"""M79: a mensagem é gravada antes de a IA trabalhar e o pedido em andamento aparece ao recarregar."""
import uuid

import pytest
from django.test import Client
from django.urls import reverse

from iativa.agent import agente
from iativa.chat import conversas
from iativa.chat.models import Mensagem

from .test_tarefas import base  # noqa: F401

pytestmark = pytest.mark.django_db


def test_mensagem_visivel_enquanto_a_ia_trabalha(base, client, monkeypatch):
    c = conversas.nova_individual(base['u'])
    pedido = uuid.uuid4()
    client.force_login(base['u'])
    visto = {}

    def conversar(user, conversa, texto, **kw):
        # Durante o trabalho: a mensagem já está gravada e a página mostra o "trabalhando".
        visto['msg'] = Mensagem.objects.filter(conversa=conversa, papel='usuario', texto=texto).exists()
        visto['pendente'] = conversas.envio_pendente(conversa) is not None
        visto['pagina'] = 'data-envio-pendente' in client.get(reverse('chat:conversa', args=[conversa.pk])).content.decode()
        return agente.Saida('Pronto.')
    monkeypatch.setattr(agente, 'conversar', conversar)
    conversas.enviar(base['u'], c, 'quantos admitidos?', pedido=pedido)
    assert visto == {'msg': True, 'pendente': True, 'pagina': True}
    assert conversas.envio_pendente(c) is None  # respondido


def test_envio_parado_ou_com_falha_tira_a_mensagem(base, monkeypatch):
    c = conversas.nova_individual(base['u'])

    def falha(*a, **kw):
        raise agente.Interrompido
    monkeypatch.setattr(agente, 'conversar', falha)
    with pytest.raises(agente.Interrompido):
        conversas.enviar(base['u'], c, 'oi', pedido=uuid.uuid4())
    assert not Mensagem.objects.filter(conversa=c).exists()


def test_nova_conversa_aparece_na_lateral_antes_da_primeira_resposta(base, client, monkeypatch, novo_usuario):
    pedido = uuid.uuid4()
    user = base['u']
    client.force_login(user)
    andamento_url = reverse('chat:andamento', args=[pedido])
    outro = Client()
    outro.force_login(novo_usuario("outra-pessoa", base["cob"]))

    def conversar(usuario, conversa, texto, **kwargs):
        assert not conversa.mensagens.filter(papel="ia").exists()
        dados = client.get(andamento_url, {"nova": "1"}).json()
        assert reverse('chat:conversa', args=[conversa.pk]) in dados["lateral"]
        assert "Nova conversa" in dados["lateral"]
        assert "lateral" not in outro.get(andamento_url, {"nova": "1"}).json()
        return agente.Saida("Pronto.")

    monkeypatch.setattr(agente, 'conversar', conversar)
    r = client.post(reverse('chat:nova'), {"mensagem": "Quantos admitidos?", "pedido": pedido}, HTTP_HX_REQUEST="true")
    assert r.status_code == 200
    assert "Pronto." in r.content.decode()
