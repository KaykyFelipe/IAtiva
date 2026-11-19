"""Incidente agente.objetivo.calculo.revisao_reprovada: em pedido de prazo entre dois eventos ("admitidos em 2026
que saíram antes de 90 dias"), o modelo calculou dias corridos de todos (sem a condição), o revisor reprovou e o
modelo repetiu a mesma ação. O backend aplica a operação exigida (intervalo_entre_datas com o limite do pedido) e
a lista traz só quem atende, com o nome. Dados sintéticos."""
import pytest

from iativa.agent import agente
from iativa.agent.provedores import FakeProvider
from iativa.chat import conversas
from iativa.query.servico import Resultado
from .test_contagem_intervalo_datas import PERGUNTA
from .test_historico_funcional import funcional  # noqa: F401
from .test_tarefas import base, _d  # noqa: F401

pytestmark = pytest.mark.django_db

REPROVA = {'natureza': 'dados', 'setores': [], 'confianca': 1, 'memorias': [],
           'resumo': 'Aplique a condição de menos de 90 dias e liste só quem atende, com nome, admissão e demissão.',
           'entregas': [{'descricao': 'Quem saiu antes de 90 dias', 'tipo': 'consulta', 'metrica': ''}]}
DIAS = {'operacao': 'dias_corridos', 'campo': 'Admissão', 'campo_final': 'Demissão'}


@pytest.fixture
def lista(funcional, monkeypatch):  # noqa: F811
    escopo = {'setor': funcional['cob'].pk, 'filiais': ['01'], 'campos': {'SRA': ['RA_ADMISSA', 'RA_DEMISSA']}}
    original = agente._agir

    def agir(*a, **kw):
        if a[2].acao == 'preparar_consulta':
            return agente.Saida('Admissões consultadas.', {}, resultado=Resultado(
                True, 'Admissões', colunas=['Filial', 'Matrícula', 'Centro de custo', 'Cargo', 'Nome', 'Admissão',
                                            'Demissão'], tipos=['texto'] * 5 + ['data', 'data'],
                linhas=[['01', '900001', 'CC1', 'AUX', 'PESSOA A', '05/01/2026', '20/02/2026'],
                        ['01', '900002', 'CC1', 'AUX', 'PESSOA B', '10/01/2026', '15/06/2026'],
                        ['01', '900003', 'CC1', 'AUX', 'PESSOA C', '01/02/2026', ''],
                        ['01', '900004', 'CC1', 'AUX', 'PESSOA D', '01/03/2026', '29/05/2026']],
                escopo=escopo))
        return original(*a, **kw)
    monkeypatch.setattr(agente, '_agir', agir)
    return funcional, conversas.nova_individual(funcional['u'])


def test_calculo_sem_condicao_vira_intervalo_com_limite_e_nome(lista):
    b, c = lista
    p = FakeProvider([_d(acao='preparar_consulta', objetivo=b['cm'].codigo, entrega='consulta'),
                      _d(acao='calcular_resultado', calculo=DIAS),
                      _d(acao='calcular_resultado', calculo=DIAS)], revisoes=[])  # só a entrega com a condição é aprovada
    msg, res = conversas.enviar(b['u'], c, PERGUNTA, p)
    assert res is not None and res.medida_calculada == 'intervalo_entre_datas', msg.texto
    assert res.curta_salva == '2' and len(res.linhas) == 2, msg.texto
    assert 'Nome' in res.colunas and res.colunas[-3:] == ['Admissão', 'Demissão', 'Dias']
