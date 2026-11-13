"""M40: a pessoa pede para ajustar o filtro de uma consulta aprendida e pode voltar como era antes."""
from io import StringIO

import pytest
from django.core.management import call_command

from iativa.accounts.models import Department
from iativa.agent import aprendizado
from iativa.agent.decisao import DecisaoInvalida, validar
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


def _d(**kw):
    base = {"resposta": "", "acao": "conversar", "objetivo": "", "parametros": [], "alvo": "", "nota": "",
            "confirmado": False, "opcoes": []}
    return {**base, **kw}


FILIAL = [{"codigo": "filial-analise", "valor": "01"}]


def _consulta(ana, c):
    p = FakeProvider([
        _d(acao="aprender_tabela", alvo="SE1 titulos"),
        _d(acao="criar_consulta", parametros=FILIAL,
           consulta={"tabela": "SE1", "nome": "Titulos ajustaveis", "colunas": ["E1_NUM", "E1_CLIENTE", "E1_VALOR"]}),
    ])
    msg, res = conversas.enviar(ana, c, "títulos a receber", p)
    assert res is not None and res.ok and res.linhas, msg.texto
    return ConsultaModelo.objects.get(nome="Titulos ajustaveis"), len(res.linhas)


def _rodar(ana, c, cm):
    msg, res = conversas.enviar(ana, c, "rode de novo", FakeProvider([
        _d(acao="preparar_consulta", objetivo=cm.codigo, parametros=FILIAL)]))
    assert res is not None and res.ok, msg.texto
    return len(res.linhas)


def test_ajusta_filtro_de_tabela_ligada_e_volta_como_era(ana):
    c = conversas.nova_individual(ana)
    cm, total = _consulta(ana, c)

    msg, _ = conversas.enviar(ana, c, "considere só clientes do comércio", FakeProvider([
        _d(acao="ajustar_filtro", alvo=cm.nome,
           ajuste={"filtros": [{"campo": "A1_XSEGMEN", "operador": "igual", "valor": "COM"}]})]))
    assert "Ajustei os filtros" in msg.texto and "A1_XSEGMEN" in msg.texto, msg.texto
    cm.refresh_from_db()
    assert cm.spec["filtros_fixos"] == [{"campo": "A1_XSEGMEN", "op": "=", "valor": "COM", "alias": "j1"}]
    assert [j["tabela"] for j in cm.spec["juncoes"]] == ["SA1"]
    filtrado = _rodar(ana, c, cm)
    assert 0 < filtrado < total

    msg, _ = conversas.enviar(ana, c, "volte o filtro como era antes", FakeProvider([
        _d(acao="voltar_filtro", alvo=cm.nome)]))
    assert "Voltei os filtros" in msg.texto, msg.texto
    cm.refresh_from_db()
    assert cm.spec["filtros_fixos"] == [] and cm.spec["juncoes"] == [] and cm.spec["anteriores"] == []
    assert _rodar(ana, c, cm) == total


def test_filtro_diferente_e_remover(ana):
    c = conversas.nova_individual(ana)
    cm, total = _consulta(ana, c)
    cm = aprendizado.ajustar_filtros(ana, cm, [{"campo": "A1_XSEGMEN", "operador": "diferente", "valor": "COM"}], [])
    sem_bloqueados = _rodar(ana, c, cm)
    assert 0 < sem_bloqueados < total
    cm = aprendizado.ajustar_filtros(ana, cm, [], ["A1_XSEGMEN"])
    assert cm.spec["filtros_fixos"] == [] and len(cm.spec["anteriores"]) == 2
    assert _rodar(ana, c, cm) == total


def test_campo_fora_da_tabela_e_das_ligadas_recusado(ana):
    c = conversas.nova_individual(ana)
    cm, _ = _consulta(ana, c)
    msg, _ = conversas.enviar(ana, c, "filtre pela TES", FakeProvider([
        _d(acao="ajustar_filtro", alvo=cm.nome, ajuste={"filtros": [{"campo": "F4_DUPLIC", "operador": "igual", "valor": "S"}]}),
        _d(resposta="Essa tabela não liga à TES."),
    ]))
    cm.refresh_from_db()
    assert cm.spec["filtros_fixos"] == [] and "anteriores" not in cm.spec


def test_voltar_sem_ajuste_explica(ana):
    c = conversas.nova_individual(ana)
    cm, _ = _consulta(ana, c)
    msg, _ = conversas.enviar(ana, c, "volte o filtro", FakeProvider([
        _d(acao="voltar_filtro", alvo=cm.nome), _d(resposta="Não havia ajuste para desfazer.")]))
    assert "desfazer" in msg.texto


def test_sem_permissao_nao_ajusta(ana, monkeypatch):
    c = conversas.nova_individual(ana)
    cm, _ = _consulta(ana, c)
    monkeypatch.setattr(aprendizado, "pode_ajustar", lambda u: False)
    msg, _ = conversas.enviar(ana, c, "filtre por tipo", FakeProvider([
        _d(acao="ajustar_filtro", alvo=cm.nome, ajuste={"filtros": [{"campo": "E1_TIPO", "operador": "igual", "valor": "NF"}]})]))
    cm.refresh_from_db()
    assert "não pode ajustar" in msg.texto and cm.spec["filtros_fixos"] == []
    assert "manutenção" not in msg.texto  # falta de permissão é impedimento, não defeito


def test_decisao_valida_ajuste():
    base = _d(acao="ajustar_filtro", alvo="x")
    with pytest.raises(DecisaoInvalida):
        validar(base, [], [])
    with pytest.raises(DecisaoInvalida):
        validar({**base, "ajuste": {"filtros": [{"campo": "X", "operador": "maior", "valor": "1"}]}}, [], [])
    d = validar({**base, "ajuste": {"filtros": [{"campo": "f4_duplic", "operador": "igual", "valor": "S"}]}}, [], [])
    assert d.ajuste == {"filtros": [{"campo": "F4_DUPLIC", "operador": "igual", "valor": "S"}], "remover": []}


def test_mesma_mensagem_cria_ajusta_e_soma(ana):
    """Caso real (M40): "faturamento é X com filtro Y; me dê o total". A consulta criada no passo 1 precisa
    estar disponível no passo seguinte; repetir aprender_tabela não pode encerrar o pedido."""
    c = conversas.nova_individual(ana)
    criar = _d(acao="criar_consulta", parametros=FILIAL, entrega="calculo",
               consulta={"tabela": "SE1", "nome": "Titulos do comercio", "colunas": ["E1_NUM", "E1_CLIENTE", "E1_VALOR"]})
    p = FakeProvider([
        _d(acao="aprender_tabela", alvo="SE1 titulos", entrega="calculo"),
        criar,
        criar,  # repetição: deve virar aviso, não fim
        _d(acao="ajustar_filtro", alvo="Titulos do comercio",
           ajuste={"filtros": [{"campo": "A1_XSEGMEN", "operador": "igual", "valor": "COM"}]}),
        _d(acao="preparar_consulta", objetivo="aprendido-titulos-do-comercio",
           parametros=FILIAL + [{"codigo": "agregacao", "valor": "soma:E1_VALOR"}]),
    ])
    msg, res = conversas.enviar(ana, c, "o total é só de clientes do comércio: some os títulos", p)
    assert res is not None and res.ok, msg.texto
    assert any("Soma" in col for col in res.colunas), (msg.texto, list(__import__("iativa.agent.models", fromlist=["x"]).Objetivo.objects.values_list("exigencia", "situacao", "trilha")))
    assert "manutenção" not in msg.texto
