"""M52: pedido de projeção não é cumprido pela soma do que já aconteceu."""
from types import SimpleNamespace

from iativa.agent import objetivos as coord


def test_reconhece_pedido_de_projecao():
    assert coord.metrica_exigida("faça uma projeção do quanto vamos faturar este mês") == "projecao"
    assert coord.metrica_exigida("qual a previsão de fechamento do mês?") == "projecao"
    assert coord.metrica_exigida("quanto faturamos este mês?") == ""


def test_soma_do_realizado_nao_cumpre_projecao():
    res = SimpleNamespace(ok=True, medida_calculada="", consolidado=False)
    saida = SimpleNamespace(ficha={"tipo": "calculo_resultado"}, observacao=None, resultado=res)
    ev = coord.evidencia(SimpleNamespace(acao="calcular_resultado"), saida,
                         "faça uma projeção do quanto vamos faturar este mês")
    assert not coord.cumpre(coord.CALCULO, ev)


def test_observacao_explica_que_falta_a_projecao():
    ac = coord.Acompanhamento("faça uma projeção do faturamento", exig=coord.CALCULO)
    assert "PROJEÇÃO" in ac.falta_obs()


def test_projecao_linear_por_dias_uteis():
    from datetime import date
    from decimal import Decimal
    from iativa.agent.calculos import _projecao
    from iativa.query.servico import Resultado
    res = Resultado(True, "", colunas=["Filial", "Soma"], tipos=["texto", "dinheiro"],
                    linhas=[["001", "1.000,00"], ["002", "3.000,00"]], premissas=[], escopo={"setor": 1})
    m = SimpleNamespace(pk=0, resultado={"truncado": False, "moedas": []})
    # 16/11/2026: passados 01..13/11 = 9 dias úteis (02/11 é feriado); mês = 19 dias úteis (02 e 20/11 feriados).
    r = _projecao(res, m, {"campo": "Soma"}, [], hoje=date(2026, 11, 16))
    assert r.medida_calculada == "projecao"
    assert r.linhas[-1] == ["Total", "R$ 8.444,44", "R$ 4.000,00"]
    assert r.curta.startswith("Projeção para 11/2026: R$ 8.444,44")
    assert any("estimativa" in p for p in r.premissas)
