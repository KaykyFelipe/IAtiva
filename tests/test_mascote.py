"""A interface do chat apresenta respostas e erros sem o mascote."""
import pytest
from django.urls import reverse
from iativa.chat import conversas
from iativa.chat.models import Mensagem
from .test_chat import _base, ana, cobranca  # noqa: F401

pytestmark = pytest.mark.django_db


def test_chat_inicial_sem_mascote(client, ana):
    client.force_login(ana)
    r = client.get(reverse('chat:index'))
    html = r.content.decode()
    assert 'agente-capsula' not in html and 'mascote-template' not in html
    assert 'js/mascote.js' not in html and 'css/mascote.css' not in html
    assert not Mensagem.objects.exists()
    assert "script-src 'self'" in r.headers['Content-Security-Policy']


def test_resposta_no_historico_tem_texto_e_acao_de_copia(client, ana):
    c = conversas.nova_individual(ana)
    Mensagem.objects.create(conversa=c, papel='ia', texto='Resposta de demonstração.')
    client.force_login(ana)
    html = client.get(reverse('chat:conversa', args=[c.pk]), HTTP_HX_REQUEST='true').content.decode()
    assert 'Resposta de demonstração.' in html and 'data-copiar-resposta' in html
    assert 'mascote-personagem' not in html and '<script' not in html


def test_respostas_preservam_a_mensagem_de_falha_no_historico(client, ana):
    c = conversas.nova_individual(ana)
    Mensagem.objects.create(conversa=c, papel='ia', texto='Indisponível.', ficha={'tipo':'falha_modelo'})
    client.force_login(ana)
    url = reverse('chat:conversa', args=[c.pk])
    assert 'Indisponível.' in client.get(url).content.decode()
    Mensagem.objects.create(conversa=c, papel='ia', texto='Consulta concluída.', ficha={'tipo':'executada'})
    html = client.get(url).content.decode()
    assert 'Indisponível.' in html and 'Consulta concluída.' in html


def test_formulario_invalido_exibe_erro_sem_executar(client, ana):
    client.force_login(ana)
    html = client.post(reverse('chat:nova'), {'mensagem':'x' * 10000}, HTTP_HX_REQUEST='true').content.decode()
    assert 'class="erro" role="alert"' in html and not Mensagem.objects.exists()
