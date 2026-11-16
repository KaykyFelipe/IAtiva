"""Dias úteis de hoje até o fim do mês, descontando feriados nacionais (datas relativas, sem ERP)."""
from datetime import date
from unittest import mock

import pytest

from iativa.agent import calculos
from iativa.agent.models import Objetivo, SituacaoObjetivo
from iativa.agent.provedores import FakeProvider
from iativa.chat import conversas
from .test_tarefas import base, _d  # noqa: F401

pytestmark = pytest.mark.django_db

PERGUNTA = 'quantos dias úteis faltam para acabar o mês, descontando feriados nacionais?'
CALC = {"operacao": "dias_uteis", "mensagem": 0, "campo": "", "campo_final": ""}


def test_intervalo_ate_fim_do_mes():
    hoje = date(2026, 11, 16)
    assert calculos.intervalo_do_texto(PERGUNTA, hoje) == (hoje, date(2026, 12, 1))
    assert calculos.intervalo_do_texto('quantos dias úteis entre 01/04/2026 e 08/04/2026?', hoje) == (
        date(2026, 4, 1), date(2026, 4, 8))
    assert calculos.intervalo_do_texto('quantos dias úteis tem?', hoje) is None


def test_dias_uteis_ate_fim_do_mes_desconta_feriados(base):
    c = conversas.nova_individual(base['u'])
    with mock.patch('django.utils.timezone.localdate', return_value=date(2026, 11, 16)):
        msg, res = conversas.enviar(base['u'], c, PERGUNTA, FakeProvider([
            _d(acao='calcular_resultado', calculo=CALC, resposta='Calculando.')]))
    assert res is not None and res.medida_calculada == 'dias_uteis', msg.texto
    # 16/11 a 30/11/2026: 11 dias de seg-sex, menos 20/11 (sexta, Consciência Negra) = 10.
    assert res.linhas[0][3] == '10'
    assert any('feriados nacionais' in p for p in res.premissas)
    assert not Objetivo.objects.exclude(situacao=SituacaoObjetivo.CUMPRIDO).exists()
