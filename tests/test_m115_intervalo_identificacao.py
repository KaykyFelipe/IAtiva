"""Incidente agente.objetivo.calculo.revisao_reprovada (eb4a3ce7ea): "admitidos em 2026 que saíram antes de 90
dias". A consulta base tinha várias colunas de texto (filial, centro de custo, departamento, cargo) antes da
matrícula e do nome; a lista de quem atende cortava a matrícula e o revisor reprovou a entrega (pedia nome
completo, matrícula, admissão e demissão). A identificação da pessoa (matrícula e nome) tem prioridade sobre
colunas descritivas. Dados sintéticos."""
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
           'resumo': 'Conte quem saiu antes de 90 dias e liste nome completo, matrícula, admissão e demissão.',
           'entregas': [{'descricao': 'Quem saiu antes de 90 dias', 'tipo': 'consulta', 'metrica': ''}]}
CALC = {'operacao': 'intervalo_entre_datas', 'campo': 'Admissão', 'campo_final': 'Demissão', 'limite': 90}


@pytest.fixture
def lista(funcional, monkeypatch):  # noqa: F811
    escopo = {'setor': funcional['cob'].pk, 'filiais': ['01'], 'campos': {'SRA': ['RA_ADMISSA', 'RA_DEMISSA']}}
    original = agente._agir

    def agir(*a, **kw):
        if a[2].acao == 'preparar_consulta':
            return agente.Saida('Admissões consultadas.', {}, resultado=Resultado(
                True, 'Admissões', colunas=['Filial', 'Centro de custo', 'Departamento', 'Cargo', 'Matrícula',
                                            'Nome completo', 'Admissão', 'Demissão'],
                tipos=['texto'] * 6 + ['data'] * 2,
                linhas=[['01', 'CC1', 'DEP', 'AUX', '900001', 'PESSOA A', '05/01/2026', '20/02/2026'],
                        ['01', 'CC1', 'DEP', 'AUX', '900002', 'PESSOA B', '10/01/2026', '15/06/2026'],
                        ['01', 'CC1', 'DEP', 'AUX', '900003', 'PESSOA C', '01/02/2026', '']],
                escopo=escopo))
        return original(*a, **kw)
    monkeypatch.setattr(agente, '_agir', agir)
    return funcional, conversas.nova_individual(funcional['u'])


def test_lista_do_intervalo_identifica_por_matricula_e_nome(lista):
    b, c = lista
    p = FakeProvider([_d(acao='preparar_consulta', objetivo=b['cm'].codigo, entrega='consulta'),
                      _d(acao='calcular_resultado', calculo=CALC)], revisoes=[])
    msg, res = conversas.enviar(b['u'], c, PERGUNTA, p)
    assert res is not None and res.medida_calculada == 'intervalo_entre_datas', msg.texto
    assert res.curta_salva == '1', msg.texto
    assert {'Matrícula', 'Nome completo'} <= set(res.colunas), res.colunas
    assert res.linhas[0][res.colunas.index('Matrícula')] == '900001'
    assert res.colunas[-3:] == ['Admissão', 'Demissão', 'Dias']
