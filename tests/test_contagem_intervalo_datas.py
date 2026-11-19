"""Incidente agente.objetivo.calculo.capacidade_declarada: contar registros cujo intervalo em dias corridos entre
duas datas do resultado é menor que um limite, ignorando registros sem data final. Dados sintéticos."""
import pytest

from iativa.agent import agente
from iativa.agent.provedores import FakeProvider
from iativa.chat import conversas
from iativa.query.servico import Resultado
from .test_historico_funcional import funcional  # noqa: F401
from .test_tarefas import base, _d  # noqa: F401

pytestmark = pytest.mark.django_db

PERGUNTA = 'Quantos admitidos em 2026 saíram antes de completar 90 dias?'


@pytest.fixture
def datas(funcional, monkeypatch):  # noqa: F811
    escopo = {'setor': funcional['cob'].pk, 'filiais': ['01'], 'campos': {'SRA': ['RA_ADMISSA', 'RA_DEMISSA']}}
    original = agente._agir

    def agir(*a, **kw):
        if a[2].acao == 'preparar_consulta':
            return agente.Saida('Admissões consultadas.', {}, resultado=Resultado(
                True, 'Admissões', colunas=['Matrícula', 'Admissão', 'Demissão'], tipos=['texto', 'data', 'data'],
                linhas=[['900001', '05/01/2026', '20/02/2026'],   # 46 dias: conta
                        ['900002', '10/01/2026', '15/06/2026'],   # 156 dias: não conta
                        ['900003', '01/02/2026', ''],             # ativo: ignorado
                        ['900004', '01/03/2026', '29/05/2026']],  # 89 dias: conta
                escopo=escopo))
        return original(*a, **kw)
    monkeypatch.setattr(agente, '_agir', agir)
    return funcional, conversas.nova_individual(funcional['u'])


def test_contagem_de_registros_com_intervalo_menor_que_limite(datas):
    b, c = datas
    msg, res = conversas.enviar(b['u'], c, PERGUNTA, FakeProvider([
        _d(acao='preparar_consulta', objetivo=b['cm'].codigo, entrega='calculo'),
        _d(acao='calcular_resultado', calculo={'operacao': 'intervalo_entre_datas', 'campo': 'Admissão',
                                               'campo_final': 'Demissão', 'limite': 90}),
        _d(acao='falta_capacidade', capacidade='contagem condicional por intervalo entre datas')]))
    assert res is not None and res.medida_calculada == 'intervalo_entre_datas', msg.texto
    assert res.curta_salva == '2', msg.texto
