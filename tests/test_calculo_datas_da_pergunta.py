"""Dias úteis entre duas datas digitadas na pergunta, descontando feriados nacionais (sem consulta prévia)."""
from datetime import date

import pytest

from iativa.agent import calculos
from iativa.agent.models import Objetivo, SituacaoObjetivo
from iativa.agent.provedores import FakeProvider
from iativa.chat import conversas
from .test_tarefas import base, _d  # noqa: F401

pytestmark = pytest.mark.django_db

PERGUNTA = ('quantos dias úteis tem entre 02/02/2026 e 27/02/2026, descontando os feriados nacionais? '
            'quero apenas os dias')


def test_feriados_nacionais_incluem_moveis():
    f = calculos.feriados_nacionais(2026)
    assert date(2026, 4, 3) in f  # Sexta-feira Santa (Páscoa em 05/04/2026)
    assert date(2026, 11, 20) in f and date(2026, 4, 21) in f
    assert date(2026, 2, 17) not in f  # Carnaval não é feriado nacional


def test_dias_uteis_entre_datas_da_pergunta_desconta_feriados(base):
    c = conversas.nova_individual(base['u'])
    # Trilha do incidente: o modelo só conversa e depois repete a mesma ação.
    msg, res = conversas.enviar(base['u'], c, PERGUNTA, FakeProvider([
        _d(resposta='São cerca de 19 dias.'), _d(resposta='São cerca de 19 dias.'),
        _d(resposta='São cerca de 19 dias.')]))
    assert res is not None and res.medida_calculada == 'dias_uteis', msg.texto
    # 02/02 a 27/02/2026 (fim excluído): 19 dias de seg-sex; nenhum feriado nacional no intervalo.
    assert res.linhas[0][3] == '19'
    assert any('feriados nacionais' in p for p in res.premissas)
    assert not Objetivo.objects.exclude(situacao=SituacaoObjetivo.CUMPRIDO).exists()
    assert 'manutenção' not in msg.texto


def test_sem_pedir_feriados_nao_desconta(base):
    c = conversas.nova_individual(base['u'])
    _, res = conversas.enviar(base['u'], c, 'quantos dias úteis entre 01/04/2026 e 08/04/2026?',
                              FakeProvider([_d(resposta='5.')]))
    assert res.linhas[0][3] == '5' and any('sem descontar feriados' in p for p in res.premissas)


def test_feriado_nacional_e_descontado():
    assert calculos.dias_uteis(date(2026, 4, 1), date(2026, 4, 8)) == 5
    assert calculos.dias_uteis(date(2026, 4, 1), date(2026, 4, 8), feriados=True) == 4
