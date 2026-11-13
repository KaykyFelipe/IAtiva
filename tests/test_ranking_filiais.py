"""M40: ranking em "todas as filiais" é geral (grupos somados entre filiais) e a resposta diz quem ficou no topo."""
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command

from iativa.accounts.models import Department
from iativa.agent.models import ConsultaModelo
from iativa.agent.provedores import FakeProvider
from iativa.chat import conversas

pytestmark = pytest.mark.django_db


def _d(**kw):
    base = {"resposta": "", "acao": "conversar", "objetivo": "", "parametros": [], "alvo": "", "nota": "",
            "confirmado": False, "opcoes": []}
    return {**base, **kw}


@pytest.fixture
def ana(erp_sintetico, settings, novo_usuario, politica):
    settings.IATIVA_METADADOS = {"fonte": "sintetico"}
    call_command("seed_exemplos", stdout=StringIO())
    cob = Department.objects.get(codigo="financeiro-cobranca")
    u = novo_usuario("ana", cob)
    politica(u, cob, consultar=True, filiais=["01", "02", "03"])
    return u


def _num(t):
    return Decimal(str(t).replace(".", "").replace(",", "."))


def _rodar(ana, agregacao, filial="TODAS"):
    c = conversas.nova_individual(ana)
    conversas.enviar(ana, c, "títulos", FakeProvider([
        _d(acao="aprender_tabela", alvo="SE1"),
        _d(acao="criar_consulta", parametros=[{"codigo": "filial-analise", "valor": "01"}],
           consulta={"tabela": "SE1", "nome": "Titulos ranking", "colunas": ["E1_NUM", "E1_CLIENTE", "E1_VALOR"]})]))
    cm = ConsultaModelo.objects.get(nome="Titulos ranking")
    return conversas.enviar(ana, c, "ranking", FakeProvider([_d(acao="preparar_consulta", objetivo=cm.codigo, parametros=[
        {"codigo": "filial-analise", "valor": filial}, {"codigo": "agregacao", "valor": agregacao}])]))


def test_top_n_geral_entre_filiais(ana):
    msg, res = _rodar(ana, "soma:E1_VALOR por E1_CLIENTE top 3")
    assert res.ok and "Filial" not in res.colunas, res.colunas
    assert len(res.linhas) == 3
    clientes = [l[0] for l in res.linhas]
    assert len(set(clientes)) == 3  # cada cliente uma vez, somado entre filiais
    valores = [_num(l[-1]) for l in res.linhas]
    assert valores == sorted(valores, reverse=True)
    assert res.mensagem.startswith("Maior "), res.mensagem


def test_agrupado_sem_top_tambem_soma_entre_filiais(ana):
    _, por_filial = _rodar(ana, "soma:E1_VALOR por E1_CLIENTE", filial="01")
    msg, res = _rodar(ana, "soma:E1_VALOR por E1_CLIENTE")
    assert "Filial" not in res.colunas
    total = sum(_num(l[-1]) for l in res.linhas)
    assert "Maior" in res.mensagem and total > 0
    assert len(res.linhas) >= len(por_filial.linhas)
