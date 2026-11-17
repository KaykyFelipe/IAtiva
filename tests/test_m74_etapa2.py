"""M74 (etapa 2): período/filial conferidos por entrega e pergunta objetiva quando falta definição."""
from types import SimpleNamespace

import pytest

from iativa.agent import definicoes, planejamento
from iativa.agent.objetivos import Acompanhamento
from iativa.agent.provedores import FakeProvider
from iativa.chat import conversas

from .test_ciclo_agente import base  # noqa: F401

pytestmark = pytest.mark.django_db

LISTA = {'descricao': 'Listar os funcionários admitidos em agosto de 2026 com nome e função', 'tipo': 'consulta',
         'metrica': '', 'campos_pedidos': ['nome', 'funcao']}


def _d(**params):
    return SimpleNamespace(parametros=tuple(params.items()), atende=(0,))


def test_periodo_da_entrega_conferido_contra_o_usado():
    assert planejamento.divergencia(LISTA, _d(**{'periodo-analise': '01/08/2026..31/08/2026'})) == ''
    motivo = planejamento.divergencia(LISTA, _d(**{'periodo-analise': '01/07/2026..31/07/2026'}))
    assert 'período pedido 01/08/2026 a 31/08/2026' in motivo
    assert planejamento.divergencia({**LISTA, 'comparacao': True}, _d(**{'periodo-analise': '01/07/2026..31/07/2026'})) == ''


def test_filial_da_entrega_conferida():
    item = {'descricao': 'Faturamento de agosto de 2026 em Cuiabá', 'tipo': 'consulta', 'metrica': ''}
    assert 'filial pedida 001002' in planejamento.divergencia(item, _d(**{'filial-analise': 'TODAS'}))
    assert planejamento.divergencia(item, _d(**{'filial-analise': '001002'})) == ''


def test_entrega_com_periodo_errado_fica_pendente_com_motivo():
    from iativa.agent.agente import Saida
    from iativa.query.servico import Resultado
    ac = Acompanhamento('x', contrato=[dict(LISTA)])
    r = Resultado(True, '', colunas=['Nome', 'Função'], tipos=['texto', 'texto'], linhas=[['a', 'b']])
    planejamento.registrar_entregas(ac, _d(**{'periodo-analise': '01/07/2026..31/07/2026'}), Saida('', resultado=r))
    assert ac.cumpridas == [] and LISTA['descricao'] in ac.divergencias
    planejamento.registrar_entregas(ac, _d(**{'periodo-analise': '01/08/2026..31/08/2026'}), Saida('', resultado=r))
    assert ac.cumpridas == [0]


def test_termo_sem_definicao_vira_pergunta_objetiva(base, settings, tmp_path):
    (tmp_path / 'g.md').write_text(
        '---\ntitulo: Glossário\ndominio: financeiro_receber\norigem: sintetico\nversao: 1\nresponsavel: TI\n'
        'situacao: aprovado\naprovado_por: x\naprovado_em: 2026-11-10\ntabelas: SE1\n---\n\n'
        '## Ticket médio\nAinda sem definição aprovada. Perguntar ao usuário: data de corte e tolerância em dias.\n',
        encoding='utf-8')
    settings.IATIVA_CONHECIMENTO_DIR = tmp_path
    c = conversas.nova_individual(base['ana'])
    termo, texto = definicoes.pergunta(base['ana'], c, 'Qual o ticket médio por filial?')
    assert termo == 'Ticket médio' and 'data de corte e tolerância em dias' in texto
    # O próprio pedido define o critério: não pergunta.
    assert definicoes.pergunta(base['ana'], c, 'Ticket médio, considere o valor das notas dividido pelos clientes') is None
    m, _ = conversas.enviar(base['ana'], c, 'Qual o ticket médio por filial?', FakeProvider([]))
    assert m.ficha.get('definicao_pedida') == 'Ticket médio'
    # Já perguntado nesta conversa: a próxima mensagem é a resposta, não pergunta de novo.
    assert definicoes.pergunta(base['ana'], c, 'ticket médio é valor por nota') is None
    assert definicoes.pergunta(base['ana'], c, 'e o ticket médio de Cuiabá?') is None
