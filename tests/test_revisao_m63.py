import pytest
from iativa.agent.provedores import LLMProvider, FakeProvider
from iativa.agent.decisao import EntradaAgente
from iativa.agent import admissoes
from iativa.chat import conversas
from .test_tarefas import base, _d

pytestmark = pytest.mark.django_db


def test_planejador_nao_valida_acao_que_nao_sera_executada():
    class Provider(LLMProvider):
        def _gerar(self, prompt, schema, validar, objetivos, parametros):
            assert set(schema['properties']) == {'analise'}
            return {'analise': {'natureza': 'geral'}}
    e=EntradaAgente('Liste minhas memórias','2026-11-17',[],[],None,planejamento={'modo':'classificar'})
    assert Provider().planejar(e)=={'natureza':'geral'}


def test_continuacao_de_periodo_preserva_nomes_e_datas(base):
    b=base;cm=b['cm'];cm.spec['campo_data']='RA_ADMISSA';cm.save(update_fields=['spec'])
    from iativa.agent.models import NotaAgente
    NotaAgente.objects.create(texto='Em perguntas sobre funcionários admitidos no mês, mostrar o nome completo e a '
                              'data de admissão de cada um.', tipo='preferencia', escopo='usuario', usuario=b['u'])
    c=conversas.nova_individual(b['u'])
    p=FakeProvider([_d(acao='preparar_consulta',objetivo=cm.codigo,parametros=[
        {'codigo':'filial-analise','valor':'TODAS'},{'codigo':'periodo-analise','valor':'01/10/2026..31/10/2026'}])])
    conversas.enviar(b['u'],c,'Quantos funcionários foram admitidos em outubro?',p)
    nova = admissoes.contextualizar(b['u'],c,'E no mês passado?')
    assert 'Quantos funcionários foram admitidos' in nova and 'mês passado' in nova and 'outubro' not in nova
    p=FakeProvider([_d(acao='preparar_consulta',objetivo=cm.codigo,parametros=[
        {'codigo':'filial-analise','valor':'TODAS'}, {'codigo':'agregacao','valor':'por filial;contagem:RA_MAT'}])])
    m,r=conversas.enviar(b['u'],c,'E no mês passado?',p)
    assert p.chamadas==1 and r is not None and admissoes.tem_detalhes(r)
    assert admissoes.contextualizar(b['u'],c,'E o faturamento de outubro?')=='E o faturamento de outubro?'
