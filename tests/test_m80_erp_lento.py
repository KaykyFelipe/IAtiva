"""M80: ERP lento (tempo esgotado) e ação repetida: continua sozinho em segundo plano, não desiste."""
import pytest

from iativa.agent import agente, objetivos as coord
from iativa.agent.models import Objetivo, SituacaoObjetivo as SO
from iativa.chat import conversas
from iativa.chat.models import Mensagem

from .test_tarefas import base  # noqa: F401

pytestmark = pytest.mark.django_db


def test_tempo_esgotado_repetido_continua_em_segundo_plano(base):
    c = conversas.nova_individual(base['u'])
    m = Mensagem.objects.create(conversa=c, papel='usuario', autor=base['u'], texto='quantos admitidos em agosto?')
    ac = coord.Acompanhamento('quantos admitidos em agosto?', exig=coord.CALCULO)
    ac.anotar('preparar_consulta', agente.TEMPO_ESGOTADO)
    ac.anotar('preparar_consulta', 'acao_repetida')
    s = agente._encerrar_sem_cumprir(ac, base['u'], c, m.pk, agente.Saida(''), None, 'acao_repetida', [])
    assert Objetivo.objects.get().situacao == SO.CONTINUAR
    assert 'segundo plano' in s.texto and 'Tente de novo' not in s.texto
