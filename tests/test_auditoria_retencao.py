"""Auditoria sanitizada e expurgo por retenção."""
from datetime import timedelta
from io import StringIO

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection
from django.utils import timezone

from iativa.audit.models import OperationAudit
from iativa.audit.servicos import registrar
from iativa.chat.models import ConversationContext, RequestContext
from iativa.chat.servicos import criar_solicitacao, transicionar
from iativa.memory import servicos as memoria
from iativa.memory.models import Escopo, ParameterBinding

pytestmark = pytest.mark.django_db

MARCADOR = "MARCADOR-SENSIVEL-91c2"


@pytest.fixture
def ana(novo_usuario, setores, politica):
    user = novo_usuario("ana", setores["cobranca"])
    politica(user, setores["cobranca"], consultar=True)
    return user


def _tabela(model) -> str:
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT * FROM "{model._meta.db_table}"')
        return repr(cursor.fetchall())


def test_auditoria_e_imutavel():
    r = registrar("expurgo", "retencao")
    r.estado_novo = "outro"
    with pytest.raises(ValidationError):
        r.save()
    with pytest.raises(ValidationError):
        r.delete()


@pytest.mark.parametrize("campo", ["estado_anterior", "estado_novo"])
def test_auditoria_so_aceita_codigos(campo):
    with pytest.raises(ValidationError):
        registrar("expurgo", "retencao", **{campo: "SELECT * FROM SE1010"})
    with pytest.raises(ValidationError):
        registrar("acao_inventada", "retencao")


def test_auditoria_nao_guarda_valores_de_parametros(ana, definicoes):
    s = criar_solicitacao(ana, "financeiro_receber", "titulos-em-atraso")
    memoria.registrar_valor(ana, definicoes["cliente"], Escopo.SOLICITACAO, MARCADOR, solicitacao=s)
    assert OperationAudit.objects.filter(acao="parametro_registrado").exists()
    assert MARCADOR not in _tabela(OperationAudit)


def _envelhecer(model, pk, **campos):
    model.objects.filter(pk=pk).update(**campos)


def test_expurgo_respeita_prazos(ana, definicoes):
    agora = timezone.now()
    # Valor com dado pessoal vencido (30 dias) em solicitação ainda ativa.
    ativa = criar_solicitacao(ana, "financeiro_receber", "titulos-em-atraso")
    pessoal = memoria.registrar_valor(ana, definicoes["cliente"], Escopo.SOLICITACAO, "000001-01", solicitacao=ativa)
    _envelhecer(ParameterBinding, pessoal.pk, expira_em=agora - timedelta(minutes=1))
    data = memoria.registrar_valor(ana, definicoes["data_corte"], Escopo.SOLICITACAO, "2025-12-31", solicitacao=ativa)

    # Solicitação encerrada há 91 dias e outra há 10 dias.
    antiga = transicionar(ana, criar_solicitacao(ana, "financeiro_receber", "a"), "cancelada")
    _envelhecer(RequestContext, antiga.pk, encerrado_em=agora - timedelta(days=91))
    _envelhecer(ConversationContext, antiga.conversa_id, atualizado_em=agora - timedelta(days=91))
    recente = transicionar(ana, criar_solicitacao(ana, "financeiro_receber", "b"), "cancelada")
    _envelhecer(RequestContext, recente.pk, encerrado_em=agora - timedelta(days=10))

    preferencia = memoria.registrar_valor(ana, definicoes["tolerancia"], Escopo.USUARIO, 5)

    saida = StringIO()
    call_command("expurgar_retencao", stdout=saida)

    assert not ParameterBinding.objects.filter(pk=pessoal.pk).exists()
    assert ParameterBinding.objects.filter(pk__in=[data.pk, preferencia.pk]).count() == 2
    assert not RequestContext.objects.filter(pk=antiga.pk).exists()
    assert not ConversationContext.objects.filter(pk=antiga.conversa_id).exists()
    assert RequestContext.objects.filter(pk__in=[ativa.pk, recente.pk]).count() == 2
    assert "valores_dado_pessoal=1" in saida.getvalue() and "solicitacoes=1" in saida.getvalue()
    assert OperationAudit.objects.filter(acao="expurgo").exists()
    assert "000001" not in saida.getvalue()


def test_simulacao_nao_remove(ana, definicoes):
    s = criar_solicitacao(ana, "financeiro_receber", "titulos-em-atraso")
    b = memoria.registrar_valor(ana, definicoes["cliente"], Escopo.SOLICITACAO, "000001-01", solicitacao=s)
    _envelhecer(ParameterBinding, b.pk, expira_em=timezone.now() - timedelta(minutes=1))
    call_command("expurgar_retencao", "--simular", stdout=StringIO())
    assert ParameterBinding.objects.filter(pk=b.pk).exists()


def test_auditoria_sem_prazo_definido_e_mantida(settings):
    registro = registrar("expurgo", "retencao")
    OperationAudit.objects.filter(pk=registro.pk).update(criado_em=timezone.now() - timedelta(days=5000))
    call_command("expurgar_retencao", stdout=StringIO())
    assert OperationAudit.objects.filter(pk=registro.pk).exists()
    settings.IATIVA_RETENCAO = {**settings.IATIVA_RETENCAO, "auditoria_dias": 365}
    call_command("expurgar_retencao", stdout=StringIO())
    assert not OperationAudit.objects.filter(pk=registro.pk).exists()


def test_seed_exemplos_idempotente():
    from iativa.accounts.models import AccessPolicy, Department
    from iativa.memory.models import ParameterDefinition

    call_command("seed_exemplos", stdout=StringIO())
    call_command("seed_exemplos", stdout=StringIO())
    assert Department.objects.count() == 3
    assert ParameterDefinition.objects.filter(contem_dado_pessoal=True).count() == 1
    assert not AccessPolicy.objects.exists()
