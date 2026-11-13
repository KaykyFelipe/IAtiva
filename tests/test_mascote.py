"""Integração do mascote: identidade, respostas e estados verdadeiros de falha."""
import pytest
from django.urls import reverse
from iativa.chat import conversas
from iativa.chat.models import Mensagem
from .test_chat import _base, ana, cobranca  # noqa: F401

pytestmark = pytest.mark.django_db


def test_capsula_e_template_disponiveis_sem_executar_modelo(client, ana):
    client.force_login(ana)
    r = client.get(reverse('chat:index'))
    html = r.content.decode()
    assert 'class="agente-capsula"' in html and 'id="mascote-template"' in html
    assert 'js/mascote.js' in html and 'css/mascote.css' in html
    assert 'aria-label="Assistente IAtiva"' in html
    assert not Mensagem.objects.exists()
    assert "script-src 'self'" in r.headers['Content-Security-Policy']


def test_resposta_no_historico_tem_mascote_e_nao_carrega_script_em_fragmento(client, ana):
    c = conversas.nova_individual(ana)
    Mensagem.objects.create(conversa=c, papel='ia', texto='Resposta de demonstração.')
    client.force_login(ana)
    html = client.get(reverse('chat:conversa', args=[c.pk]), HTTP_HX_REQUEST='true').content.decode()
    assert 'assinatura-ia' in html and 'mascote-personagem' in html
    assert '<script' not in html and 'id="mascote-template"' not in html


def test_falha_nao_e_conclusao_e_falha_antiga_nao_contamina_resposta_nova(client, ana):
    c = conversas.nova_individual(ana)
    Mensagem.objects.create(conversa=c, papel='ia', texto='Indisponível.', ficha={'tipo':'falha_modelo'})
    client.force_login(ana)
    url = reverse('chat:conversa', args=[c.pk])
    assert 'data-falha="1"' in client.get(url).content.decode()
    Mensagem.objects.create(conversa=c, papel='ia', texto='Consulta concluída.', ficha={'tipo':'executada'})
    assert 'data-falha="0"' in client.get(url).content.decode()


def test_formulario_invalido_nao_comemora_sem_executar(client, ana):
    client.force_login(ana)
    html = client.post(reverse('chat:nova'), {'mensagem':'x' * 10000}, HTTP_HX_REQUEST='true').content.decode()
    assert 'data-falha="1"' in html and not Mensagem.objects.exists()
