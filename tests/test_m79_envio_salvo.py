"""M79: a mensagem é gravada antes de a IA trabalhar e o pedido em andamento aparece ao recarregar."""
import uuid

import pytest
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
