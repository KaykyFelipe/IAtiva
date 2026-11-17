"""M81 (etapa 4): conhecimento por domínio e fonte nova autorizada só pela TI."""
import pytest
from django.http import QueryDict

from iativa.accounts.models import Department
from iativa.agent import aprendizado, memoria
from iativa.agent.models import NotaAgente, SituacaoModelo

from .test_ciclo_agente import base  # noqa: F401

pytestmark = pytest.mark.django_db


def test_nota_do_setor_vale_para_quem_consulta_o_dominio(base, novo_usuario, politica):
    cob = base['cob']
    rh = Department.objects.get_or_create(codigo='rh', defaults={'nome': 'Recursos Humanos'})[0]
    nota = NotaAgente.objects.create(texto='Faturamento deve ser calculado pela consulta de itens de NF.',
                                     tipo='termo', escopo='setor', setor=cob)
    ti = novo_usuario('analista-ti', Department.objects.get(codigo='ti'))
    politica(ti, cob, consultar=True, filiais=['01'])  # de outro setor, mas consulta a Cobrança
    so_rh = novo_usuario('so-rh', rh)
    politica(so_rh, rh, consultar=True, filiais=['01'])
    assert nota in memoria.notas_validas(ti)
    assert nota not in memoria.notas_validas(so_rh)


def test_fonte_nova_de_quem_nao_e_da_ti_fica_proposta(base):
    ana = base['ana']  # consulta a Cobrança, não é da TI
    cm = aprendizado.criar_automatico(ana, {'tabela': 'SE1', 'nome': 'Títulos novos', 'colunas': ['E1_NUM', 'E1_VALOR']})
    assert cm.situacao == SituacaoModelo.PROPOSTA
    assert not aprendizado.pode_aprovar(ana, cm)
    assert aprendizado.pode_aprovar(base['admin'], cm)  # administrador da TI


def test_mesma_fonte_proposta_nao_duplica_e_a_ti_aprova(base):
    from iativa.agent.models import ConsultaModelo
    pedido = {'tabela': 'SE1', 'nome': 'Títulos novos', 'colunas': ['E1_NUM', 'E1_VALOR']}
    a = aprendizado.criar_automatico(base['ana'], pedido)
    assert aprendizado.criar_automatico(base['ana'], pedido).pk == a.pk  # não duplica
    b = aprendizado.criar_automatico(base['admin'], pedido)  # TI pede o mesmo: aprova a proposta
    assert b.pk == a.pk and b.situacao == SituacaoModelo.APROVADA
    assert ConsultaModelo.objects.filter(nome='Títulos novos').count() == 1
