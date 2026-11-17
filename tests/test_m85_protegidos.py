"""M85: pedido de dado pessoal protegido responde na hora, sem procurar tabela."""
import pytest

from iativa.agent import protegidos
from iativa.agent.provedores import FakeProvider
from iativa.chat import conversas

from .test_ciclo_agente import base  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize('texto,dado', [
    ('liste os aniversários desse mês pelo setor de RH', 'data de nascimento'),
    ('qual o CPF do funcionário João?', 'documento pessoal (CPF, RG, PIS, CTPS)'),
    ('qual o salário dos analistas?', 'salário'),
    ('quantos funcionários foram admitidos em agosto?', ''),
    ('qual o faturamento de agosto?', ''),
])
def test_reconhece_dado_protegido(texto, dado):
    assert protegidos.protegido(texto) == dado


def test_responde_na_hora_sem_chamar_o_modelo_para_agir(base):
    p = FakeProvider([])
    m, r = conversas.enviar(base['ana'], conversas.nova_individual(base['ana']), 'liste os aniversários desse mês', p)
    assert r is None and 'data de nascimento' in m.texto and 'protegido' in m.texto
    assert m.ficha.get('dado_protegido') == 'data de nascimento'
