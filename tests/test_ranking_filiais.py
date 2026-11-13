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


def test_total_por_filial_diz_a_maior(ana):
    msg, res = _rodar(ana, "soma:E1_VALOR")
    assert res.colunas[0] == "Filial" and len(res.linhas) == 3
    assert res.mensagem.startswith("Maior "), res.mensagem


def test_top_1_responde_direto(ana):
    msg, res = _rodar(ana, "soma:E1_VALOR por E1_CLIENTE top 1")
    assert len(res.linhas) == 1 and res.mensagem.startswith("O maior é "), res.mensagem
    assert "Ranking com os 1" not in res.mensagem


def _atraso(ana, extra):
    c = conversas.nova_individual(ana)
    return conversas.enviar(ana, c, "inadimplentes", FakeProvider([_d(acao="preparar_consulta", objetivo="titulos-em-atraso", parametros=[
        {"codigo": "filial-analise", "valor": "TODAS"}, {"codigo": "data-corte", "valor": "13/11/2026"},
        {"codigo": "dias-tolerancia-atraso", "valor": "0"},
        {"codigo": "tratamento-baixa-parcial", "valor": "considerar-saldo"}, *extra])]))


def test_inadimplencia_top_n_geral_pelo_valor(ana):
    """M41: "os 10 maiores inadimplentes" em todas as filiais: top N geral, ordenado e destacado pelo valor."""
    _, todos = _atraso(ana, [])
    msg, res = _atraso(ana, [{"codigo": "agregacao", "valor": "top 2"}])
    assert res is not None, msg.texto
    assert res.ok and len(res.linhas) == 2 and "Filial" not in res.colunas, (res.colunas, res.mensagem)
    i = res.colunas.index("Valor em atraso")
    valores = [_num(str(l[i]).replace("R$", "").strip()) for l in res.linhas]
    assert valores == sorted(valores, reverse=True)
    assert res.mensagem.startswith("Maior Valor em atraso"), res.mensagem
    assert f"({res.linhas[0][i]})" in res.mensagem.split(". Menor")[0], res.mensagem
    assert len(todos.linhas) > 2


def test_inadimplencia_recusa_calculo_que_nao_e_top(ana):
    msg, res = _atraso(ana, [{"codigo": "agregacao", "valor": "soma:E1_VALOR"}])
    assert res is None and "top N" in msg.texto, msg.texto


def test_total_da_consulta_fixa_responde_quanto(ana):
    """M41: "quanto vamos receber até o fim do mês?" — a lista fixa já traz o total do banco; não cria consulta nova."""
    c = conversas.nova_individual(ana)
    p = FakeProvider([_d(acao="preparar_consulta", objetivo="titulos-a-vencer", entrega="calculo", parametros=[
        {"codigo": "filial-analise", "valor": "TODAS"}, {"codigo": "periodo-analise", "valor": "01/01/2020..31/12/2030"},
        {"codigo": "tratamento-baixa-parcial", "valor": "considerar-saldo"}])] * 3)
    msg, res = conversas.enviar(ana, c, "quanto vamos receber até o fim do mês?", p)
    assert res is not None and res.ok, msg.texto
    assert p.chamadas == 1, p.chamadas
    assert not ConsultaModelo.objects.exists()


def test_mensagem_com_duas_perguntas_responde_as_duas(ana):
    """M41: "quanto vamos receber? e quanto está em atraso?" não para na primeira resposta."""
    from iativa.agent import objetivos as coord

    assert coord.partes("e quantos foram admitidos este ano? e quantos saíram?") == 2
    assert coord.partes("quantos funcionários ativos temos hoje em cada filial?") == 1
    c = conversas.nova_individual(ana)
    comum = [{"codigo": "filial-analise", "valor": "TODAS"},
             {"codigo": "tratamento-baixa-parcial", "valor": "considerar-saldo"}]
    p = FakeProvider([
        _d(acao="preparar_consulta", objetivo="titulos-a-vencer", entrega="calculo",
           parametros=comum + [{"codigo": "periodo-analise", "valor": "01/01/2020..31/12/2030"}]),
        _d(acao="preparar_consulta", objetivo="titulos-em-atraso", entrega="calculo",
           parametros=comum + [{"codigo": "data-corte", "valor": "13/11/2026"},
                               {"codigo": "dias-tolerancia-atraso", "valor": "0"}]),
        _d(acao="conversar", resposta="Já respondi as duas.")])
    msg, res = conversas.enviar(ana, c, "quanto vamos receber? e quanto está em atraso?", p)
    assert p.chamadas == 2, (p.chamadas, msg.texto)
    assert res is not None and res.ok
    assert "títulos, somando" in msg.texto, msg.texto  # o número da 1ª pergunta continua visível


def test_segunda_pergunta_sem_consulta_nao_vira_resposta(ana):
    """M41: depois da 1ª resposta, "já rodei as duas" sem executar não aparece para a pessoa."""
    c = conversas.nova_individual(ana)
    p = FakeProvider([
        _d(acao="preparar_consulta", objetivo="titulos-a-vencer", entrega="calculo", parametros=[
            {"codigo": "filial-analise", "valor": "TODAS"}, {"codigo": "tratamento-baixa-parcial", "valor": "considerar-saldo"},
            {"codigo": "periodo-analise", "valor": "01/01/2020..31/12/2030"}]),
        _d(acao="conversar", resposta="Já rodei as duas consultas.")])
    msg, res = conversas.enviar(ana, c, "quanto vamos receber? e quanto está em atraso?", p)
    assert p.chamadas == 2 and res is not None and res.ok
    assert "Já rodei" not in msg.texto, msg.texto
