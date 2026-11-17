"""Lista de funcionários com a função atual não é pedido de histórico de cargo (incidente acao_repetida)."""
from iativa.agent import historico_funcional, objetivos
from types import SimpleNamespace

PERGUNTA = "RH: quais funcionários estão ativos hoje, por filial? Mostre nome completo, função e data de admissão."


def test_lista_de_funcionarios_com_funcao_nao_exige_historico():
    assert not historico_funcional.solicitado(PERGUNTA)
    d = SimpleNamespace(acao="preparar_consulta", entrega="", parametros=())
    exig, _ = objetivos.exigencia(PERGUNTA, d)
    assert exig != objetivos.HISTORICO
    # pedido de histórico continua exigindo histórico
    assert historico_funcional.solicitado("qual o histórico de cargo do Fulano de Tal?")
    assert historico_funcional.solicitado("quais funções o Fulano de Tal já teve?")
    assert historico_funcional.solicitado("histórico de função dos funcionários da filial 01")
