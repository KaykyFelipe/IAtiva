"""M133: "quantos pedidos em aberto, por filial" rodou a contagem distinta por filial no banco, mas a conferência só
olhava medida_calculada/totais e recusava a entrega (o pedido ia para o segundo plano e a manutenção)."""
from types import SimpleNamespace

from iativa.agent.agente import Saida
from iativa.agent.decisao import Decisao
from iativa.agent.planejamento import conferir
from iativa.query.servico import Resultado

ANALISE = {"entregas": [{"descricao": "Quantidade de pedidos em aberto por filial", "tipo": "calculo",
                         "metrica": "contagem"}]}


def _saida(funcoes):
    r = Resultado(True, "", colunas=["Filial", "Pedidos"], tipos=["texto", "inteiro"], linhas=[["01", "3"]])
    r.agregado, r.funcoes = True, funcoes
    return Saida("", {"tipo": "executada"}, None, r)


def _d():
    return Decisao("", "preparar_consulta", "x", (("agregacao", "contagem_distinta:C7_NUM por C7_FILIAL"),),
                   "", "", False, ())


def test_contagem_distinta_por_filial_cumpre_a_contagem():
    assert conferir(ANALISE, _d(), _saida(["COUNT_DISTINCT"])) == []


def test_soma_nao_cumpre_contagem():
    assert conferir(ANALISE, _d(), _saida(["SUM"])) == ["Quantidade de pedidos em aberto por filial"]
