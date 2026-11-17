"""M77 (etapa 3): envio idempotente, um pedido por chat e fila justa entre usuários."""
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from iativa.agent import objetivos as coord
from iativa.agent.models import Objetivo, SituacaoObjetivo as SO
from iativa.agent.provedores import FakeProvider
from iativa.chat import conversas
from iativa.chat.models import Mensagem

from .test_objetivos import _pedido_que_nao_avanca
from .test_tarefas import _d, base  # noqa: F401

pytestmark = pytest.mark.django_db


def test_mesmo_envio_nao_roda_duas_vezes(base):
    c = conversas.nova_individual(base['u'])
    pedido = uuid.uuid4()
    p = FakeProvider([_d(resposta='Olá! Como posso ajudar?')])
    m1, _ = conversas.enviar(base['u'], c, 'oi', p, pedido=pedido)
    m2, _ = conversas.enviar(base['u'], c, 'oi', p, pedido=pedido)
    assert m1.pk == m2.pk and p.chamadas == 1
    assert Mensagem.objects.filter(conversa=c, papel='usuario').count() == 1


def test_chat_com_agente_trabalhando_recusa_outro_pedido(base):
    _pedido_que_nao_avanca(base)
    o = Objetivo.objects.get()
    Objetivo.objects.filter(pk=o.pk).update(situacao=SO.CONTINUAR, execucao='x',
                                            lease_ate=timezone.now() + timedelta(minutes=2))
    with pytest.raises(ValidationError, match='ainda está trabalhando'):
        conversas.enviar(o.usuario, o.conversa, 'outra pergunta', FakeProvider([]))


def test_fila_justa_e_limite_por_usuario(monkeypatch, settings):
    settings.IATIVA_AGENTES_POR_USUARIO = 2
    monkeypatch.setattr(coord, 'usuarios_ocupados', lambda agora=None: __import__('collections').Counter({1: 2, 2: 1}))
    itens = [SimpleNamespace(pk=1, usuario_id=1), SimpleNamespace(pk=2, usuario_id=2),
             SimpleNamespace(pk=3, usuario_id=3), SimpleNamespace(pk=4, usuario_id=2)]
    # Usuário 1 já tem 2 rodando (limite); o 3 (nenhum rodando) vem antes do 2 (um rodando).
    assert [x.pk for x in coord.justa(itens)] == [3, 2, 4]


def test_indice_unico_parcial_de_campo_novo_e_aditivo():
    import importlib
    from django.db import migrations, models
    from iativa.core.management.commands.verificar_migracoes import avaliar_operacao
    mig = importlib.import_module('iativa.chat.migrations.0006_mensagem_pedido').Migration
    assert all(avaliar_operacao(mig, op) == '' for op in mig.operations)
    # Índice único sobre campo antigo (ou sem condição) continua bloqueado.
    velha = migrations.AddConstraint('mensagem', models.UniqueConstraint(fields=['conversa', 'texto'], name='x'))
    assert avaliar_operacao(mig, velha) != ''
