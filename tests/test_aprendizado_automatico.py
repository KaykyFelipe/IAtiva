"""M18: a IA cria sozinha a consulta que falta, sem aprovação humana, e já executa."""
from io import StringIO

import pytest
from django.core.management import call_command

from iativa.accounts.models import Department
from iativa.agent import aprendizado
from iativa.agent.models import ConsultaModelo, SituacaoModelo
from iativa.agent.provedores import FakeProvider
from iativa.chat import conversas

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _base(erp_sintetico, settings):
    settings.IATIVA_METADADOS = {"fonte": "sintetico"}
    call_command("seed_exemplos", stdout=StringIO())


@pytest.fixture
def ana(novo_usuario, politica):
    cob = Department.objects.get(codigo="financeiro-cobranca")
    u = novo_usuario("ana", cob)  # sem administrar: só consulta
    politica(u, cob, consultar=True, filiais=["01"])
    return u


def _d(**kw):
    base = {"resposta": "", "acao": "conversar", "objetivo": "", "parametros": [], "alvo": "", "nota": "",
            "confirmado": False, "opcoes": []}
    return {**base, **kw}


def test_ia_cria_consulta_sozinha_e_executa(ana):
    p = FakeProvider([
        _d(acao="aprender_tabela", alvo="SA1 clientes"),
        _d(acao="criar_consulta", resposta="Vou listar os clientes.",
           parametros=[{"codigo": "filial-analise", "valor": "01"}],
           consulta={"tabela": "SA1", "nome": "Clientes cadastrados", "colunas": ["A1_COD", "A1_NOME", "A1_CGC"]}),
    ])
    c = conversas.nova_individual(ana)
    msg, res = conversas.enviar(ana, c, "quais clientes temos cadastrados?", p)
    cm = ConsultaModelo.objects.get(tabela="SA1")
    assert cm.situacao == SituacaoModelo.APROVADA and cm.aprovado_por == ana
    assert [x["campo"] for x in cm.spec["colunas"]] == ["A1_COD", "A1_NOME"]  # CPF/CNPJ descartado
    assert "Aprendi" in msg.texto
    assert res is not None and res.ok and res.linhas


def test_reaproveita_consulta_igual(ana):
    consulta = {"tabela": "SA1", "nome": "Clientes", "descricao": "", "colunas": ["A1_COD", "A1_NOME"],
                "campo_data": "", "campo_busca": "", "filtraveis": []}
    a = aprendizado.criar_automatico(ana, consulta)
    b = aprendizado.criar_automatico(ana, consulta)
    assert a.pk == b.pk


def test_sem_consulta_valida_nao_quebra(ana):
    p = FakeProvider([_d(acao="aprender_tabela", alvo="SA1"),
                      _d(acao="criar_consulta", consulta={"tabela": "SA1", "nome": "X", "colunas": ["A1_CGC"]})])
    c = conversas.nova_individual(ana)
    msg, res = conversas.enviar(ana, c, "clientes?", p)
    assert res is None and msg.texto and not ConsultaModelo.objects.exists()
