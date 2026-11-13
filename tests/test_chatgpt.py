import json
import os
from pathlib import Path
from queue import Queue, Empty
from types import SimpleNamespace
import time

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import Client
from django.urls import reverse

from iativa.agent import codex_login as cg, conexoes, claude_login as cl
from iativa.agent.codex_provider import CodexProvider, schema_estrito
from iativa.agent.codex_rpc import ambiente
from iativa.agent.provedores import FalhaModelo, obter_provedor
from iativa.agent.decisao import EntradaAgente
from .test_claude_login import admin, _ti, esperar  # noqa: F401

pytestmark = pytest.mark.django_db
CATALOGO = [{'model': 'modelo-teste', 'displayName': 'Modelo fictício', 'supportedReasoningEfforts': [
    {'reasoningEffort': 'low'}, {'reasoningEffort': 'high'}]}]
DECISAO = {'resposta': 'Olá!', 'acao': 'conversar', 'objetivo': '', 'parametros': [], 'alvo': '',
           'nota': '', 'confirmado': False, 'opcoes': []}


@pytest.fixture
def fake(tmp_path, settings, monkeypatch):
    settings.IATIVA_AGENTE = {'provedor': 'teste', 'envio_liberado': False,
        'codex_contas_dir': str(tmp_path / 'chatgpt'), 'claude_contas_dir': str(tmp_path / 'claude'),
        'claude_manutencao_dir': str(tmp_path / 'claude-manutencao')}
    settings.IATIVA_CONEXOES_DIR = tmp_path / 'conexoes'
    instancias = []
    class Server:
        def __init__(self, user, perfil='atendimento'):
            self.root, self.cwd = cg.pasta(user, perfil), str(tmp_path / 'isolado')
            self.root.mkdir(parents=True, exist_ok=True)
            self.calls, self.events = [], Queue()
            self.answer = DECISAO
            self.timeout = 25
            instancias.append(self)
        def __enter__(self):
            return self
        def __exit__(self, *a):
            self.close()
        def close(self):
            pass
        def autenticado(self):
            return (self.root / 'fake-auth').exists()
        def request(self, method, params=None, **kw):
            self.calls.append((method, params))
            if method == 'account/login/start':
                return {'type':'chatgptDeviceCode', 'loginId':'login-ficticio',
                        'verificationUrl':'https://auth.openai.com/codex/device', 'userCode':'ABCD-1234'}
            if method == 'account/logout':
                (self.root / 'fake-auth').unlink(missing_ok=True)
                return {}
            if method == 'model/list':
                return {'data': CATALOGO, 'nextCursor': None}
            if method == 'thread/start':
                return {'thread': {'id': 'thread-ficticia'}}
            if method == 'turn/start':
                self.events.put({'method':'item/completed', 'params':{'item':{'type':'agentMessage', 'text':json.dumps(self.answer)}}})
                self.events.put({'method':'turn/completed', 'params':{'turn':{'status':'completed'}}})
                return {'turn': {'id':'turn-ficticio'}}
            return {}
        def evento(self, timeout):
            try:
                return self.events.get(timeout=min(timeout, .05))
            except Empty:
                raise FalhaModelo('aguardando', temporaria=True)
    monkeypatch.setattr(cg, 'rpc', Server)
    manager = cg.LoginChatGPT()
    monkeypatch.setattr(cg, 'login', manager)
    yield SimpleNamespace(manager=manager, instancias=instancias, Server=Server, tmp=tmp_path)
    manager.fechar()


def conectar(fake, user, perfil='atendimento', sessao='sessao-a'):
    estado = fake.manager.iniciar(user, sessao, perfil)
    f = fake.manager.obter(user, sessao, estado['id'], perfil)
    esperar(lambda: f.estado()['fase'] == 'aguardando')
    (f.server.root / 'fake-auth').touch()
    f.server.events.put({'method':'account/login/completed', 'params':{'loginId':'login-ficticio', 'success':True}})
    esperar(lambda: f.estado()['fase'] == 'conectado')
    return f


def test_login_device_code_isolado_e_codigo_nao_persistido(admin, fake):
    f = conectar(fake, admin)
    assert cg.ativa(admin) and not cg.ativa(admin, 'manutencao')
    assert not f.estado()['codigo'] and not f.estado()['url']
    assert cg.modelos(admin, 'atendimento')[0]['id'] == 'modelo-teste'
    for path in fake.tmp.rglob('*'):
        if path.is_file():
            assert 'ABCD-1234' not in path.read_text()


def test_contas_e_preferencias_separadas_por_agente_e_usuario(admin, fake, django_user_model):
    outro = _ti(django_user_model.objects.create_user('outro-chatgpt'))
    conectar(fake, admin)
    conectar(fake, admin, 'manutencao')
    cg.salvar_preferencias(admin, 'manutencao', 'modelo-teste', 'high')
    assert cg.preferencias(admin, 'atendimento') == {'modelo':'', 'esforco':''}
    assert cg.preferencias(outro, 'manutencao')['esforco'] == 'high'
    assert not cg.ativa(outro)
    assert not cl.ativa(admin) and not cl.ativa(admin, 'manutencao')


def test_nao_troca_atendimento_ao_selecionar_manutencao(admin, fake, monkeypatch):
    conectar(fake, admin)
    conectar(fake, admin, 'manutencao')
    conexoes.escolher(admin, 'atendimento', 'chatgpt')
    # A seleção de manutenção é outro arquivo, sem tocar na seleção pessoal.
    conexoes.gravar_json(conexoes.arquivo(admin, 'manutencao'), {'provedor':'claude'})
    assert conexoes.selecionado(admin) == 'chatgpt'
    assert conexoes.selecionado(admin, 'manutencao') == 'claude'


def test_selecao_exige_login_sem_fallback(admin, fake, settings):
    with pytest.raises(FalhaModelo, match='Conecte'):
        conexoes.escolher(admin, 'atendimento', 'chatgpt')
    conectar(fake, admin)
    conexoes.escolher(admin, 'atendimento', 'chatgpt')
    settings.IATIVA_AGENTE['envio_liberado'] = True
    assert obter_provedor(usuario=admin).nome == 'chatgpt'
    cg.marcar(admin, 'atendimento', False)
    with pytest.raises(ImproperlyConfigured, match='Conecte'):
        obter_provedor(usuario=admin)


def test_modelo_e_esforco_precisam_vir_do_catalogo(admin, fake):
    conectar(fake, admin)
    with pytest.raises(FalhaModelo):
        cg.salvar_preferencias(admin, 'atendimento', 'modelo-inventado', 'high')
    with pytest.raises(FalhaModelo):
        cg.salvar_preferencias(admin, 'atendimento', 'modelo-teste', 'ultra')
    cg.salvar_preferencias(admin, 'atendimento', 'modelo-teste', 'low')
    assert cg.preferencias(admin, 'atendimento')['esforco'] == 'low'


def test_provedor_usa_schema_ephemero_e_sem_acesso_ao_ambiente(admin, fake, settings):
    conectar(fake, admin)
    settings.IATIVA_AGENTE['envio_liberado'] = True
    cg.salvar_preferencias(admin, 'atendimento', 'modelo-teste', 'high')
    p = CodexProvider(admin)
    entrada = EntradaAgente('oi', '2026-11-12', [], [], None)
    assert p.decidir(entrada) == DECISAO
    calls = dict(fake.instancias[-1].calls)
    assert calls['thread/start']['ephemeral'] and calls['thread/start']['environments'] == []
    assert calls['thread/start']['dynamicTools'] == [] and calls['thread/start']['model'] == 'modelo-teste'
    assert calls['turn/start']['effort'] == 'high'
    assert calls['turn/start']['sandboxPolicy']['access']['includePlatformDefaults'] is False
    assert calls['turn/start']['outputSchema']['additionalProperties'] is False


def test_privacidade_continua_obrigatoria(admin, fake):
    conectar(fake, admin)
    with pytest.raises(ImproperlyConfigured, match='privacidade'):
        CodexProvider(admin)


def test_fluxo_nao_pode_ser_lido_por_outra_sessao(admin, fake, django_user_model):
    outro = _ti(django_user_model.objects.create_user('outra-sessao'))
    f = conectar(fake, admin)
    with pytest.raises(FalhaModelo):
        fake.manager.obter(outro, 'sessao-a', f.id, 'atendimento')
    with pytest.raises(FalhaModelo):
        fake.manager.obter(admin, 'sessao-b', f.id, 'atendimento')


def test_ui_duas_opcoes_e_permissoes(admin, fake, client, usuario):
    client.force_login(admin)
    html = client.get(reverse('claude-conta')).content.decode()
    assert html.count('>ChatGPT</h3>') == 2 and 'Agente de manutenção' in html
    assert 'Conectar ChatGPT' in html and 'Padrão da conta/CLI' in html
    r = client.post(reverse('chatgpt-conta'), {'acao':'preferencias', 'perfil':'../etc'})
    assert r.status_code == 400
    client.force_login(usuario)
    assert client.get(reverse('chatgpt-conta')).status_code == 403
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(admin)
    assert strict.post(reverse('chatgpt-conta'), {'acao':'conectar'}).status_code == 403


@pytest.mark.parametrize('url', ['https://auth.openai.com.evil.test/codex/device',
    'http://auth.openai.com/codex/device', 'https://user:pw@auth.openai.com/codex/device',
    'https://auth.openai.com/codex/device?token=foo', 'javascript:alert(1)'])
def test_link_de_login_restrito_ao_oficial(url):
    assert not cg.link_oficial(url)


def test_ambiente_nao_herda_segredos(tmp_path, monkeypatch):
    monkeypatch.setenv('IATIVA_ERP_PASSWORD', 'segredo-ficticio')
    monkeypatch.setenv('OPENAI_API_KEY', 'chave-ficticia')
    monkeypatch.setenv('CODEX_ACCESS_TOKEN', 'token-ficticio')
    env = ambiente(tmp_path)
    assert env['CODEX_HOME'] == str(tmp_path)
    assert not {'IATIVA_ERP_PASSWORD','OPENAI_API_KEY','CODEX_ACCESS_TOKEN'} & env.keys()


def test_schema_estrito_preserva_opcionais_com_null():
    s = schema_estrito({'type':'object', 'properties': {'a':{'type':'string'}, 'b':{'type':'object',
        'properties':{'c':{'type':'string'}}}}, 'required':['a']})
    assert s['required'] == ['a','b'] and s['additionalProperties'] is False
    assert s['properties']['b']['anyOf'][1] == {'type':'null'}


def test_device_code_na_tela_so_para_sessao_dona(admin, fake, client):
    client.force_login(admin)
    r = client.post(reverse('chatgpt-conta'), {'acao':'conectar', 'perfil':'manutencao'})
    f = fake.manager.fluxos[(admin.pk, 'manutencao')]
    esperar(lambda: f.estado()['fase'] == 'aguardando')
    url = reverse('chatgpt-estado', args=[f.id]) + '?perfil=manutencao'
    html = client.get(url).content.decode()
    assert 'ABCD-1234' in html and 'https://auth.openai.com/codex/device' in html
    assert 'no-store' in r['Cache-Control']
    outro = Client()
    outro.force_login(admin)
    denied = outro.get(url)
    assert denied.status_code == 400 and 'ABCD-1234' not in denied.content.decode()
    client.post(reverse('chatgpt-conta'), {'acao':'cancelar','perfil':'manutencao','fluxo':f.id})
    assert not f.estado()['codigo'] and not cg.ativa(admin, 'manutencao')


def test_login_manutencao_compartilhado_nao_e_sobrescrito(admin, fake, django_user_model):
    outro = _ti(django_user_model.objects.create_user('outra-ti-chatgpt'))
    fake.manager.iniciar(admin, 'a', 'manutencao')
    with pytest.raises(FalhaModelo, match='outra sessão'):
        fake.manager.iniciar(outro, 'b', 'manutencao')
    with pytest.raises(FalhaModelo, match='Cancele'):
        fake.manager.desconectar(outro, 'b', 'manutencao')


def test_login_confirmado_expoe_selecao_sem_recarregar(admin, fake, client):
    client.force_login(admin)
    client.post(reverse('chatgpt-conta'), {'acao':'conectar','perfil':'atendimento'})
    f = fake.manager.fluxos[(admin.pk, 'atendimento')]
    esperar(lambda: f.estado()['fase'] == 'aguardando')
    (f.server.root / 'fake-auth').touch()
    f.server.events.put({'method':'account/login/completed','params':{'loginId':'login-ficticio','success':True}})
    esperar(lambda: f.estado()['fase'] == 'conectado')
    r = client.get(reverse('chatgpt-estado', args=[f.id]))
    assert r.status_code == 200 and 'Usar ChatGPT neste agente' in r.content.decode()


def test_account_read_recusa_api_e_respostas_desconhecidas():
    from iativa.agent.codex_rpc import AppServer
    s = AppServer.__new__(AppServer)
    for account in ({'type':'apiKey'}, {'type':'chatgptAuthTokens'}, []):
        s.request = lambda *a, account=account, **kw: {'account':account}
        with pytest.raises(FalhaModelo, match='API'):
            s.autenticado()


def test_rpc_nega_pedidos_de_ferramentas():
    from iativa.agent.codex_rpc import AppServer
    s = AppServer.__new__(AppServer)
    s.fila = Queue()
    s.fila.put({'id':123, 'method':'item/commandExecution/requestApproval','params':{'command':'nao executar'}})
    enviados = []
    s.enviar = enviados.append
    assert s._receber(1) == {}
    assert enviados[0]['error']['code'] == -32601

def test_selecao_claude_mostra_confirmacao_no_provedor_correto(admin,fake,client,monkeypatch):
    cl._marcar(cl.config_usuario(admin,'atendimento'),True)
    monkeypatch.setattr(cl,'provedor_login',lambda *a:object())
    monkeypatch.setattr(cl,'conectado',lambda *a:True)
    client.force_login(admin)
    r=client.post(reverse('chatgpt-conta'),{'acao':'usar','perfil':'atendimento','provedor':'claude'})
    c=r.context['cartoes'][0]
    assert c['provedor_atual']=='claude' and c['provedor_definido']
    assert 'Claude está em uso' in c['estado']['mensagem']
    assert not c['chatgpt']['estado']
    assert conexoes.preferido(admin,'manutencao')==''


def test_polling_atualiza_resumo_apenas_do_agente_escolhido(admin,fake,client):
    client.force_login(admin)
    r=client.post(reverse('chatgpt-conta'),{'acao':'conectar','perfil':'manutencao'})
    estado=r.context['cartoes'][1]['chatgpt']['estado']
    esperar(lambda:fake.instancias and fake.instancias[-1].root.name=='manutencao')
    r=client.get(reverse('chatgpt-estado',args=[estado['id']]),{'perfil':'manutencao'})
    html=r.content.decode()
    assert 'id="ia-resumo-manutencao"' in html and 'hx-swap-oob="outerHTML"' in html
    assert 'id="ia-resumo-atendimento"' not in html


def test_tela_separa_agentes_e_nao_faz_chamadas_ao_abrir(admin,fake,client):
    client.force_login(admin)
    r=client.get(reverse('claude-conta'));html=r.content.decode()
    assert 'id="agente-atendimento"' in html and 'id="agente-manutencao"' in html
    assert html.count('class="ia-provider"')==4
    assert 'css/conexoes.css' in html and not fake.instancias
    assert 'Salvar modelo Claude' in html and 'Salvar modelo ChatGPT' in html
