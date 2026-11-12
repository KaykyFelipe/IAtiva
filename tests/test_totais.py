"""Totais de colunas numéricas: calculados, não exibidos, e só enviados à IA na pergunta seguinte."""
from io import StringIO

import pytest
from django.core.management import call_command
from django.http import QueryDict
from django.urls import reverse

from iativa.accounts.models import Department
from iativa.agent import aprendizado
from iativa.agent.provedores import FakeProvider
from iativa.chat import conversas

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin(erp_sintetico, settings, novo_usuario, politica):
    settings.IATIVA_METADADOS = {"fonte": "sintetico"}
    settings.IATIVA_APRENDIZADO_AUTOAPROVACAO = True
    call_command("seed_exemplos", stdout=StringIO())
    cob = Department.objects.get(codigo="financeiro-cobranca")
    u = novo_usuario("diego", cob)
    politica(u, cob, consultar=True, administrar=True, aprovar_conhecimento=True, filiais=["01"])
    return u


def test_totais_vao_para_a_ia_sem_aparecer(admin, client):
    q = QueryDict(mutable=True)
    q.update({"nome": "Titulos"})
    q.setlist("colunas", ["E1_NUM", "E1_VALOR"])
    q.setlist("setores", [str(Department.objects.get(codigo="financeiro-cobranca").pk)])
    cm = aprendizado.aprovar(admin, aprendizado.salvar_proposta(admin, "SE1", q))
    c = conversas.nova_individual(admin)
    p = FakeProvider([{"resposta": "", "acao": "preparar_consulta", "objetivo": cm.codigo, "alvo": "", "nota": "",
                       "confirmado": False, "opcoes": [], "parametros": [{"codigo": "filial-analise", "valor": "01"}]}])
    msg, res = conversas.enviar(admin, c, "liste os títulos", p)
    assert res.ok and res.total and "Totais" not in res.mensagem and "Totais" not in msg.texto

    vistos = {}

    class Espiao(FakeProvider):
        def decidir(self, entrada):
            vistos["r"] = entrada.resultados_na_tela
            return {"resposta": "Deu X.", "acao": "conversar", "objetivo": "", "parametros": [], "alvo": "",
                    "nota": "", "confirmado": False, "opcoes": []}

    conversas.enviar(admin, c, "some os valores", Espiao(), resultados=[f"{cm.codigo}: ok Totais: {res.total}"])
    assert "Totais" in vistos["r"][0]
