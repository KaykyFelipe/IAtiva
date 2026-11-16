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
