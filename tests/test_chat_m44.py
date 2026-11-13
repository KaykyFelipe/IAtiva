"""M44: sessões, snapshots, permissões, retenção e duração de estágio."""
from datetime import date, timedelta
from io import StringIO
import sqlite3

import pytest
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from iativa.accounts.models import AccessPolicy
from iativa.agent import historico_funcional as hf
from iativa.agent.provedores import FakeProvider
from iativa.chat import conversas
from iativa.chat.models import ConversationContext, Mensagem
from iativa.metadata.models import RestricaoMetadado

from .test_historico_funcional import funcional  # noqa: F401
from .test_tarefas import base, _d  # noqa: F401

pytestmark = pytest.mark.django_db


def _historico(b, c):
    return conversas.enviar(b['u'], c, 'histórico de cargo do BRUNO SINTETICO LIMA',
        FakeProvider([_d(acao='historico_cargo', alvo='BRUNO SINTETICO LIMA')]))


def test_tabela_persiste_em_outra_sessao_sem_nova_consulta(funcional, monkeypatch):
    c = conversas.nova_individual(funcional['u'])
    msg, res = _historico(funcional, c)
    msg.refresh_from_db()
    assert msg.resultado['linhas'] == res.linhas
    cliente = Client()
    cliente.force_login(funcional['u'])
    monkeypatch.setattr(hf, 'consultar', lambda *a, **k: pytest.fail('Reabrir não consulta o ERP'))
    html = cliente.get(reverse('chat:conversa', args=[c.pk])).content.decode()
    assert 'Estagiário' in html and 'Analista de sistemas' in html and '<table>' in html
    assert 'SELECT ' not in str(msg.resultado)


def test_snapshot_nao_vai_para_contexto_do_modelo(funcional):
    c = conversas.nova_individual(funcional['u'])
    _historico(funcional, c)
    from iativa.agent.agente import _historico as contexto
    assert 'Analista de sistemas' not in str(contexto(c, None))


@pytest.mark.parametrize('mudanca', ['politica', 'campo', 'filial'])
def test_snapshot_reconfere_permissoes_atuais(funcional, mudanca):
    c = conversas.nova_individual(funcional['u'])
    _historico(funcional, c)
    if mudanca == 'politica':
        AccessPolicy.objects.filter(user=funcional['u']).update(pode_consultar=False)
    elif mudanca == 'campo':
        RestricaoMetadado.objects.create(tabela='SRA', campo='RA_NOME', ativo=True)
    else:
        AccessPolicy.objects.filter(user=funcional['u']).update(filiais=['02'])
    cliente = Client()
    cliente.force_login(funcional['u'])
    html = cliente.get(reverse('chat:conversa', args=[c.pk])).content.decode()
    assert 'Estagiário' not in html and 'Analista de sistemas' not in html
    assert 'Resultado indisponível' in html


def test_snapshot_nao_expira_com_a_conversa(funcional):
    c = conversas.nova_individual(funcional['u'])
    msg, _ = _historico(funcional, c)
    ConversationContext.objects.filter(pk=c.pk).update(atualizado_em=timezone.now() - timedelta(days=400))
    call_command('expurgar_retencao', stdout=StringIO())
    assert ConversationContext.objects.filter(pk=c.pk).exists()
    assert Mensagem.objects.get(pk=msg.pk).resultado['linhas']


def test_sincronizacao_descobre_conversa_e_mensagem_de_outra_sessao(funcional):
    cliente = Client()
    cliente.force_login(funcional['u'])
    url = reverse('chat:sincronizar')
    antes = cliente.get(url).json()
    c = conversas.nova_individual(funcional['u'])
    _historico(funcional, c)
    depois = cliente.get(url, {'versao': antes['versao']}).json()
    assert depois['versao'] != antes['versao'] and c.titulo in depois['lateral']
    atual = cliente.get(url, {'conversa': c.pk}).json()
    assert '<table>' in atual['conversa'] and 'Estagiário' in atual['conversa']
    sem_mudanca = cliente.get(url, {'conversa': c.pk, 'versao': atual['versao']}).json()
    assert set(sem_mudanca) == {'versao'}


def test_sync_nao_mostra_conversa_alheia(funcional, novo_usuario):
    c = conversas.nova_individual(funcional['u'])
    _historico(funcional, c)
    outro = novo_usuario('outro', funcional['cob'])
    cliente = Client()
    cliente.force_login(outro)
    assert cliente.get(reverse('chat:sincronizar'), {'conversa': c.pk}).status_code == 404
    assert 'BRUNO' not in cliente.get(reverse('chat:sincronizar')).content.decode()


def test_versao_historico_estavel_entre_pagina_sync_e_mudanca_so_na_lateral(funcional):
    import re
    cliente = Client()
    cliente.force_login(funcional['u'])
    c = conversas.nova_individual(funcional['u'])
    _historico(funcional, c)
    def versao(html):
        return re.search(r'data-historico-versao="([a-f0-9]+)"', html)[1]
    inicial = versao(cliente.get(reverse('chat:conversa', args=[c.pk])).content.decode())
    sync = lambda: cliente.get(reverse('chat:sincronizar'), {'conversa': c.pk}).json()['conversa']
    assert versao(sync()) == inicial
    conversas.nova_individual(funcional['u'])
    assert versao(sync()) == inicial
    AccessPolicy.objects.filter(user=funcional['u']).update(pode_consultar=False)
    sem_acesso = sync()
    assert versao(sem_acesso) != inicial and 'Estagiário' not in sem_acesso


def test_versao_historico_muda_com_nova_mensagem(client, funcional):
    import re
    c = conversas.nova_individual(funcional['u'])
    client.force_login(funcional['u'])
    url = reverse('chat:conversa', args=[c.pk])
    antes = re.search(r'data-historico-versao="(.*?)"', client.get(url).content.decode())[1]
    Mensagem.objects.create(conversa=c, papel='ia', texto='Atualização sintética.')
    depois = re.search(r'data-historico-versao="(.*?)"', client.get(url).content.decode())[1]
    assert antes != depois


def test_layout_duravel_e_independente_da_sessao(base):
    a, b = Client(), Client()
    for cli in (a, b):
        cli.force_login(base['u'])
    assert a.post(reverse('chat:layout'), {'largura': '377', 'ampla': '1'}).status_code == 204
    html = b.get(reverse('chat:index')).content.decode()
    assert 'data-largura="377"' in html and 'leitura-ampla' in html


@pytest.mark.parametrize('a,b,esperado', [(date(2026, 1, 1), date(2026, 10, 1), '9 meses'),
    (date(2026, 1, 31), date(2026, 2, 28), '1 mês'),
    (date(2024, 2, 29), date(2025, 3, 1), '12 meses e 1 dia')])
def test_duracao_usa_meses_de_calendario(a, b, esperado):
    assert hf._duracao(a, b).startswith(esperado)


def test_tempo_estagio_calcula_e_mostra_tabela_na_mesma_resposta(funcional, erp_sintetico):
    with sqlite3.connect(erp_sintetico) as con:
        con.execute("UPDATE SRA010 SET RA_ADMISSA='20260101', RA_DEMISSA='20261001' WHERE RA_MAT='000102' AND RA_FILIAL='01'")
        con.execute("DELETE FROM SR7010")
        con.execute("INSERT INTO SR7010 VALUES ('01','000102','20260101','01','00004','',' ')")
    c = conversas.nova_individual(funcional['u'])
    msg, res = conversas.enviar(funcional['u'], c,
        'qual é o tempo de estágio do funcionário BRUNO SINTETICO LIMA?',
        FakeProvider([_d(acao='conversar', resposta='Vou buscar as matrículas.')]))
    assert res and res.ok, msg.texto
    assert '9 meses' in res.mensagem and 'Estagiário' in str(res.linhas) and 'Analista' in str(res.linhas)
    assert 'Tempo de estágio registrado' in res.mensagem
    assert msg.resultado['linhas']


def test_cadastro_sem_historico_rotula_duracao_como_estimativa(funcional, erp_sintetico):
    with sqlite3.connect(erp_sintetico) as con:
        con.execute('DELETE FROM SR7010')
    res = hf.consultar(funcional['u'], None, 'BRUNO SINTETICO LIMA', tempo_estagio=True)
    assert 'estimativa' in res.mensagem
    assert 'Tempo de estágio registrado' not in res.mensagem


def test_snapshot_omite_documentos_sem_armazenar_sql():
    from iativa.chat.resultados import empacotar
    from iativa.query.servico import Resultado
    r = Resultado(True, 'Resultado', colunas=['Nome', 'Documento'], tipos=['texto', 'texto'],
                  linhas=[['PESSOA SINTETICA', '123.456.789-09']], escopo={'setor': 1})
    pacote = empacotar(r)
    assert pacote['linhas'][0][1] == '[documento omitido]'
    assert '123.456.789-09' not in str(pacote)


def test_migracao_permite_insercao_da_versao_anterior():
    from importlib import import_module
    from iativa.core.management.commands.verificar_migracoes import avaliar_operacao
    m = import_module('iativa.chat.migrations.0005_conversationcontext_preservar_historico_and_more')
    assert all(not avaliar_operacao(m, op) for op in m.Migration.operations)
