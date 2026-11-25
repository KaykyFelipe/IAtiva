import json
from types import SimpleNamespace

import pytest
from django.urls import reverse

from iativa.agent import gemini_cli
from iativa.agent.decisao import EntradaAgente
from iativa.agent.provedores import FalhaModelo, obter_provedor
from .test_chatgpt import DECISAO, fake
from .test_claude_login import admin


@pytest.mark.django_db
def test_conectar_selecionar_e_desconectar_sem_expor_a_chave(client, admin, fake, settings, monkeypatch):
    settings.IATIVA_AGENTE['envio_liberado'] = True
    client.force_login(admin)
    chave = 'chave-sintetica-para-teste-123'
    chamadas = []

    def executar(cmd, **kw):
        assert chave not in ' '.join(cmd) and chave not in kw['input']
        assert kw['env']['GEMINI_API_KEY'] == chave
        assert '--admin-policy' in cmd and 'none' in cmd
        assert 'toolName = "*"' in (kw['cwd'].parent / 'deny.toml').read_text()
        chamadas.append(kw['input'])
        return SimpleNamespace(returncode=0, stdout=json.dumps({'response': json.dumps(DECISAO)}))

    monkeypatch.setattr(gemini_cli.subprocess, 'run', executar)
    url = reverse('gemini-conta')
    base = {'perfil': 'atendimento'}
    r = client.post(url, {**base, 'acao': 'conectar', 'chave': chave, 'modelo': 'gemini-teste'})
    assert 'Conexão Gemini verificada' in r.content.decode() and chave.encode() not in r.content
    assert client.post(url, {**base, 'acao': 'usar'}).status_code == 200
    provedor = obter_provedor(admin)
    assert provedor.nome == 'gemini-cli'
    assert provedor.decidir(EntradaAgente('Olá', '2026-11-25', [], [], None)) == DECISAO
    assert len(chamadas) == 2
    assert client.post(url, {'perfil': 'manutencao', 'acao': 'usar'}).status_code == 403
    client.post(url, {**base, 'acao': 'desconectar'})
    assert not gemini_cli.cartao(admin)['ativo'] and obter_provedor(admin).nome != 'gemini-cli'


def test_resposta_com_execucao_de_ferramenta_e_recusada(settings, monkeypatch):
    settings.IATIVA_AGENTE['envio_liberado'] = True
    monkeypatch.setattr(gemini_cli.subprocess, 'run', lambda *a, **k: SimpleNamespace(
        returncode=0, stdout='{"response":"OK","stats":{"tools":{"totalCalls":1}}}'))
    with pytest.raises(FalhaModelo, match='resposta inválida'):
        gemini_cli._executar('chave-ficticia', '', 'teste')
