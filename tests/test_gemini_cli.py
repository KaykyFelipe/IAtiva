import json
from types import SimpleNamespace

import pytest
from django.urls import reverse

from iativa.agent import gemini_cli, gemini_login
from iativa.agent.decisao import EntradaAgente
from iativa.agent.provedores import FalhaModelo, obter_provedor
from .test_chatgpt import DECISAO, fake
from .test_claude_login import admin

GOOGLE = ('https://accounts.google.com/o/oauth2/v2/auth?'
          'redirect_uri=https%3A%2F%2Fcodeassist.google.com%2Fauthcode&state=teste&code_challenge=teste')


@pytest.mark.django_db
def test_login_google_e_modelo_sem_expor_credenciais(client, admin, fake, settings, monkeypatch, tmp_path):
    settings.IATIVA_AGENTE['envio_liberado'] = True
    client.force_login(admin)
    segredo = 'refresh-sintetico'
    chamadas = []

    def conectar(user, sessao, projeto):
        origem = tmp_path / 'nativo'
        origem.mkdir()
        (origem / 'oauth_creds.json').write_text(json.dumps({'refresh_token': segredo}))
        gemini_cli.salvar_conexao(user, origem, projeto)
        return {'fase': 'conectado', 'mensagem': 'Conta Google conectada.'}

    def executar(cmd, **kw):
        assert segredo not in ' '.join(cmd) and segredo not in kw['input']
        assert 'GEMINI_API_KEY' not in kw['env']
        raiz = kw['cwd'].parent
        assert json.loads((raiz / '.gemini/oauth_creds.json').read_text())['refresh_token'] == segredo
        assert '--admin-policy' in cmd and 'none' in cmd
        assert 'toolName = "*"' in (raiz / 'deny.toml').read_text()
        chamadas.append(kw['input'])
        return SimpleNamespace(returncode=0, stdout=json.dumps({'response': json.dumps(DECISAO)}))

    monkeypatch.setattr(gemini_login, 'iniciar', conectar)
    monkeypatch.setattr(gemini_cli.subprocess, 'run', executar)
    url = reverse('gemini-conta')
    base = {'perfil': 'atendimento'}
    r = client.post(url, {**base, 'acao': 'conectar'})
    assert 'Conta Google conectada' in r.content.decode() and segredo.encode() not in r.content
    r = client.post(url, {**base, 'acao': 'modelo', 'modelo': 'gemini-teste'})
    assert 'Modelo Gemini verificado' in r.content.decode()
    assert client.post(url, {**base, 'acao': 'usar'}).status_code == 200
    provedor = obter_provedor(admin)
    assert provedor.nome == 'gemini-cli' and provedor.modelo == 'gemini-teste'
    assert provedor.decidir(EntradaAgente('Olá', '2026-11-25', [], [], None)) == DECISAO
    assert len(chamadas) == 2
    assert client.post(url, {'perfil': 'manutencao', 'acao': 'usar'}).status_code == 403
    client.post(url, {**base, 'acao': 'desconectar'})
    assert not gemini_cli.cartao(admin)['ativo'] and obter_provedor(admin).nome != 'gemini-cli'


def test_login_restringe_link_e_codigo_a_sessao(monkeypatch):
    assert gemini_login.link_oficial(GOOGLE) == GOOGLE
    assert not gemini_login.link_oficial(GOOGLE.replace('codeassist.google.com', 'example.com'))
    user = SimpleNamespace(pk=123)
    fluxo = gemini_login.Fluxo(user, 'sessao', '', fase='aguardando', url=GOOGLE, terminal=4)
    monkeypatch.setattr(gemini_login, '_FLUXOS', {user.pk: fluxo})
    comandos = []
    monkeypatch.setattr(gemini_login.os, 'write', lambda fd, bytes: comandos.append((fd, bytes)))
    with pytest.raises(FalhaModelo, match='outra sessão'):
        gemini_login.concluir(user, 'outra', fluxo.id, '4/codigo-teste')
    estado = gemini_login.concluir(user, 'sessao', fluxo.id, '4/codigo-teste')
    assert estado['fase'] == 'confirmando' and not estado['url']
    assert comandos == [(4, b'4/codigo-teste\n')]
    with pytest.raises(FalhaModelo, match='utilizado'):
        gemini_login.concluir(user, 'sessao', fluxo.id, '4/codigo-teste')
