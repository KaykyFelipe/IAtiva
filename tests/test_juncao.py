"""Junções nas consultas criadas pela IA: só pelo dicionário (SX9) e pela chave única do outro lado."""
from io import StringIO

import pytest
from django.core.management import call_command

from iativa.accounts.models import Department
from iativa.agent import aprendizado
from iativa.agent.models import ConsultaModelo
from iativa.agent.provedores import FakeProvider
from iativa.chat import conversas

pytestmark = pytest.mark.django_db


@pytest.fixture
def ana(erp_sintetico, settings, novo_usuario, politica):
    settings.IATIVA_METADADOS = {"fonte": "sintetico"}
    call_command("seed_exemplos", stdout=StringIO())
    cob = Department.objects.get(codigo="financeiro-cobranca")
    u = novo_usuario("ana", cob)
    politica(u, cob, consultar=True, filiais=["01"])
    return u


def test_juncao_possivel_pela_chave(ana):
    js = {j["tabela"]: j for j in aprendizado.juncoes_possiveis(ana, "SE1")}
    assert "SA1" in js
    assert sorted(map(tuple, js["SA1"]["pares"])) == [("A1_COD", "E1_CLIENTE"), ("A1_LOJA", "E1_LOJA")]
    assert all(c.nome != "A1_CGC" for c in js["SA1"]["campos"])  # sensível fica de fora


def test_sem_juncao_que_duplica(ana):
    # SE5 aponta para SE1, mas SE1 não tem relacionamento "para cima" seguro a partir de SA1.
    assert aprendizado.juncoes_possiveis(ana, "SA1") == []


def _d(**kw):
    base = {"resposta": "", "acao": "conversar", "objetivo": "", "parametros": [], "alvo": "", "nota": "",
            "confirmado": False, "opcoes": []}
    return {**base, **kw}


def test_ia_cria_consulta_com_nome_do_cliente_e_soma_por_nome(ana):
    c = conversas.nova_individual(ana)
    p = FakeProvider([
        _d(acao="aprender_tabela", alvo="SE1 titulos"),
        _d(acao="criar_consulta", parametros=[{"codigo": "filial-analise", "valor": "01"}],
           consulta={"tabela": "SE1", "nome": "Titulos com nome do cliente", "colunas": ["E1_NUM", "E1_CLIENTE", "E1_VALOR"],
                     "juncoes": [{"tabela": "SA1", "colunas": ["A1_NOME", "A1_CGC"]}]}),
    ])
    msg, res = conversas.enviar(ana, c, "títulos com o nome do cliente", p)
    assert res is not None and res.ok and res.linhas, msg.texto
    assert any("SINTETICO" in " ".join(l) for l in res.linhas)  # nome veio do SA1
    cm = ConsultaModelo.objects.get(nome="Titulos com nome do cliente")
    assert [c["campo"] for c in cm.spec["colunas"] if c.get("alias")] == ["A1_NOME"]  # CNPJ descartado
    total_linhas = len(res.linhas)

    p2 = FakeProvider([_d(acao="preparar_consulta", objetivo=cm.codigo, parametros=[
        {"codigo": "filial-analise", "valor": "01"}, {"codigo": "agregacao", "valor": "soma:E1_VALOR por A1_NOME"}])])
    msg, res = conversas.enviar(ana, c, "some por cliente", p2)
    assert res is not None and res.ok, msg.texto
    assert res.colunas[0] == cm.spec["colunas"][-1]["rotulo"] and 0 < len(res.linhas) <= total_linhas
