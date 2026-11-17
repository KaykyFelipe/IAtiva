"""M82: assunto sem setor cadastrado (ex.: compras) vai para a busca no dicionário, não para "esclareça"."""
import pytest
from django.core.exceptions import ValidationError

from iativa.agent import planejamento
from iativa.agent.provedores import FakeProvider
from iativa.chat import conversas

from .test_ciclo_agente import base  # noqa: F401

pytestmark = pytest.mark.django_db
TEXTO = 'qual foi a última solicitação de compra feita pelo Fernando'


def _analise(natureza, setores, confianca=0.6):
    return {'natureza': natureza, 'confianca': confianca, 'setores': setores, 'entregas': [], 'resumo': ''}


@pytest.mark.parametrize('analise', [_analise('ambigua', []), _analise('dados', ['compras'], 0.9)])
def test_assunto_sem_setor_explora_o_dicionario(base, monkeypatch, analise):
    monkeypatch.setattr(planejamento, '_explorar_dicionario', lambda user, texto: True)
    c = conversas.nova_individual(base['admin'])
    a = planejamento.preparar(base['admin'], c, TEXTO, FakeProvider([], analises=[analise]), [])
    assert a['explorar_dicionario'] and a['natureza'] == 'dados' and 'aprender_tabela' in a['orientacao']


def test_sem_tabela_no_dicionario_continua_pedindo_esclarecimento(base, monkeypatch):
    monkeypatch.setattr(planejamento, '_explorar_dicionario', lambda user, texto: False)
    c = conversas.nova_individual(base['admin'])
    with pytest.raises(ValidationError, match='esclarecer'):
        planejamento.preparar(base['admin'], c, TEXTO, FakeProvider([], analises=[_analise('ambigua', [])]), [])


def test_continuacao_do_mesmo_setor_nao_pede_esclarecimento(base, monkeypatch):
    """M84: confiança baixa, mas setor válido e mesmo assunto da última consulta da conversa: segue."""
    monkeypatch.setattr(planejamento, '_explorar_dicionario', lambda user, texto: False)
    monkeypatch.setattr(planejamento, '_continua_assunto', lambda conversa, alvos: True)
    c = conversas.nova_individual(base['admin'])
    a = planejamento.preparar(base['admin'], c, 'tem observação do item X?',
                              FakeProvider([], analises=[_analise('dados', [base['cob'].codigo], 0.6)]), [])
    assert a['natureza'] == 'dados'
    monkeypatch.setattr(planejamento, '_continua_assunto', lambda conversa, alvos: False)
    with pytest.raises(ValidationError, match='esclarecer'):
        planejamento.preparar(base['admin'], c, 'tem observação do item X?',
                              FakeProvider([], analises=[_analise('dados', [base['cob'].codigo], 0.6)]), [])
