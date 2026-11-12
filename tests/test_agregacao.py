"""Cálculos nas consultas aprendidas: o banco soma, conta, tira média e agrupa; a IA só escolhe o cálculo."""
from io import StringIO

import pytest
from django.core.management import call_command
from django.http import QueryDict

from iativa.accounts.models import Department
from iativa.agent import aprendizado
from iativa.agent.provedores import FakeProvider
from iativa.chat import conversas
from iativa.query.plano import ler_agregacao

pytestmark = pytest.mark.django_db

SPEC = {"campo_data": "E1_EMISSAO", "colunas": [
    {"campo": "E1_CLIENTE", "rotulo": "Cliente", "tipo": "texto"},
    {"campo": "E1_EMISSAO", "rotulo": "Emissão", "tipo": "data"},
    {"campo": "E1_VALOR", "rotulo": "Valor", "tipo": "numero"}]}


@pytest.mark.parametrize("texto,esperado", [
    ("soma:E1_VALOR", ([("SUM", "E1_VALOR")], "", 0)),
    ("média:e1_valor por mes", ([("AVG", "E1_VALOR")], "MES", 0)),
    ("soma:E1_VALOR, contagem:* por E1_CLIENTE top 5", ([("SUM", "E1_VALOR"), ("COUNT", "*")], "E1_CLIENTE", 5)),
])
def test_ler_agregacao(texto, esperado):
    assert ler_agregacao(texto, SPEC) == esperado


@pytest.mark.parametrize("texto", ["soma:E1_CLIENTE", "soma:XX_NADA", "apagar:E1_VALOR", "soma:E1_VALOR por XX"])
def test_agregacao_invalida(texto):
    with pytest.raises(ValueError):
        ler_agregacao(texto, SPEC)


@pytest.fixture
def admin(erp_sintetico, settings, novo_usuario, politica):
    settings.IATIVA_METADADOS = {"fonte": "sintetico"}
    settings.IATIVA_APRENDIZADO_AUTOAPROVACAO = True
    call_command("seed_exemplos", stdout=StringIO())
    cob = Department.objects.get(codigo="financeiro-cobranca")
    u = novo_usuario("diego", cob)
    politica(u, cob, consultar=True, administrar=True, aprovar_conhecimento=True, filiais=["01"])
    return u


def _consulta(admin):
    q = QueryDict(mutable=True)
    q.update({"nome": "Titulos emitidos", "campo_data": "E1_EMISSAO"})
    q.setlist("colunas", ["E1_CLIENTE", "E1_EMISSAO", "E1_VALOR"])
    q.setlist("setores", [str(Department.objects.get(codigo="financeiro-cobranca").pk)])
    return aprendizado.aprovar(admin, aprendizado.salvar_proposta(admin, "SE1", q))


def _pedir(admin, cm, agregacao):
    c = conversas.nova_individual(admin)
    p = FakeProvider([{"resposta": "", "acao": "preparar_consulta", "objetivo": cm.codigo, "alvo": "", "nota": "",
                       "confirmado": False, "opcoes": [], "parametros": [
                           {"codigo": "filial-analise", "valor": "01"},
                           {"codigo": "periodo-analise", "valor": "01/01/2000..31/12/2030"},
                           {"codigo": "agregacao", "valor": agregacao}]}])
    return conversas.enviar(admin, c, "x", p)


def test_soma_total_numa_linha(admin):
    cm = _consulta(admin)
    msg, res = _pedir(admin, cm, "soma:E1_VALOR")
    assert res is not None and res.ok, msg.texto
    assert res.colunas == ["Soma de Vlr.Título"] and len(res.linhas) == 1


def test_soma_por_cliente_top(admin):
    cm = _consulta(admin)
    msg, res = _pedir(admin, cm, "soma:E1_VALOR, contagem:* por E1_CLIENTE top 2")
    assert res is not None and res.ok, msg.texto
    assert res.colunas == [res.colunas[0], "Soma de Vlr.Título", "Quantidade"] and 0 < len(res.linhas) <= 2


def test_por_mes(admin):
    cm = _consulta(admin)
    msg, res = _pedir(admin, cm, "media:E1_VALOR por mes")
    assert res is not None and res.ok, msg.texto
    assert res.colunas[0] == "Mês" and "/" in res.linhas[0][0]


def test_calculo_invalido_avisa(admin):
    cm = _consulta(admin)
    msg, res = _pedir(admin, cm, "soma:E1_CLIENTE")
    assert "Cálculo inválido" in msg.texto
