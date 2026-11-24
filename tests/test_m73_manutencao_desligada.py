"""M73: manutenção automática desligada (custo) e agentes paralelos no worker, um por chat."""
from datetime import timedelta

import pytest
from django.test import override_settings
from django.utils import timezone

from iativa.agent import objetivos as coord
from iativa.agent.models import Objetivo, SituacaoObjetivo as SO
from iativa.manutencao.models import Incidente

from .test_objetivos import _pedido_que_nao_avanca
from .test_tarefas import base  # noqa: F401

pytestmark = pytest.mark.django_db


@override_settings(IATIVA_MANUTENCAO_AUTOMATICA=False)
def test_sem_manutencao_nao_abre_registro_nem_pede_a_ti(base):
    # M133 (Diego): manutenção desligada = nenhum registro de correção; a IA entrega ou explica o que tentou.
    msg, res, p, c = _pedido_que_nao_avanca(base)
    o = Objetivo.objects.get()
    assert o.situacao == SO.IMPEDIDO  # não fica "aguardando correção"
    assert not Incidente.objects.exists()
    assert "Comunique" not in msg.texto and "corrigindo" not in msg.texto.lower()


def test_chat_com_agente_trabalhando_nao_e_pego_por_outro(base):
    _pedido_que_nao_avanca(base)
    o = Objetivo.objects.get()
    agora = timezone.now()
    Objetivo.objects.filter(pk=o.pk).update(situacao=SO.CONTINUAR, execucao='x', lease_ate=agora + timedelta(minutes=2))
    assert o.conversa_id in coord.conversas_ocupadas(agora)
    Objetivo.objects.filter(pk=o.pk).update(lease_ate=agora - timedelta(seconds=1))
    assert o.conversa_id not in coord.conversas_ocupadas(agora)
