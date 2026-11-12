"""Nomes das filiais: reconhecer pelo nome e mostrar ao lado do código."""
import pytest

from iativa.accounts.filiais import codigo_por_texto, rotulo
from iativa.agent.conversao import converter
from iativa.memory.models import TipoValor


@pytest.mark.parametrize("texto,esperado", [
    ("Cuiabá", "001002"), ("cuiaba", "001002"), ("filial de Ribeirão Preto", "001008"),
    ("na filial Campo Grande", "001005"), ("001004", "001004"), ("001004 · Limeira", "001004"),
    ("Araçatuba.", "001006"), ("Eficaz Araçatuba", "002002"), ("eficaz londrina matriz", "002001"), ("Matriz", "001001"), ("Curitiba", None), ("009999", None), ("", None),
])
def test_codigo_por_texto(texto, esperado):
    assert codigo_por_texto(texto) == esperado


def test_rotulo():
    assert rotulo("001007") == "001007 · Londrina"
    assert rotulo("01") == "01"


def test_converter_filial_por_nome():
    class D:
        tipo = TipoValor.FILIAL

    assert converter(D, "Uberlândia") == "001003"
    assert converter(D, "01") == "01"


def test_todas_por_texto():
    from iativa.accounts.filiais import TODAS

    assert codigo_por_texto("todas") == TODAS and codigo_por_texto("todas as filiais") == TODAS
    assert rotulo(TODAS) == "Todas as filiais"


@pytest.mark.django_db
def test_consulta_em_todas_as_filiais(erp_sintetico, settings, novo_usuario, politica):
    from io import StringIO

    from django.core.management import call_command

    from iativa.accounts.models import Department
    from iativa.agent.provedores import FakeProvider
    from iativa.chat import conversas

    settings.IATIVA_METADADOS = {"fonte": "sintetico"}
    call_command("seed_exemplos", stdout=StringIO())
    cob = Department.objects.get(codigo="financeiro-cobranca")
    u = novo_usuario("ana", cob)
    politica(u, cob, consultar=True, filiais=["01", "02"])
    c = conversas.nova_individual(u)
    _, um = conversas.enviar(u, c, "inadimplentes corte 2026-01-01, 0 dias, considerar saldo, filial 01")
    p = FakeProvider([{"resposta": "", "acao": "preparar_consulta", "objetivo": "titulos-em-atraso",
                       "parametros": [{"codigo": "filial-analise", "valor": "TODAS"}], "alvo": "", "nota": "",
                       "confirmado": False, "opcoes": []}])
    msg, res = conversas.enviar(u, c, "e de todas as filiais?", p)
    assert res is not None and res.ok, msg.texto
    assert res.colunas[0] == "Filial" and len(res.linhas) >= len(um.linhas)
    assert any("Todas as filiais" in x["valor"] for x in msg.ficha["criterios"])
