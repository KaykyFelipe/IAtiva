"""M108: pedido com condição de tempo entre dois eventos exige cálculo; lista de um evento não é entrega."""
from types import SimpleNamespace

import pytest

from iativa.agent import andamento, objetivos as coord


@pytest.mark.parametrize("texto", [
    "Quantos admitidos em 2026 saíram antes de completar 90 dias?",
    "quais funcionários contratados este ano foram demitidos em menos de 3 meses?",
    "quantos títulos foram pagos mais de 30 dias depois do vencimento?",
])
def test_intervalo_entre_eventos_exige_calculo(texto):
    assert coord.exigencia(texto) == (coord.CALCULO, "detector")
    assert coord.metrica_exigida(texto) == "intervalo_entre_datas"
    lista = SimpleNamespace(ok=True, medida_calculada="", linhas=[["1"]] * 5, colunas=["Matrícula"], totais=None)
    d = SimpleNamespace(objetivo="aprendido-x", parametros=())
    assert not coord.cumpre(coord.CALCULO, coord.evidencia(d, SimpleNamespace(ficha={}, observacao=None, resultado=lista), texto))


@pytest.mark.parametrize("texto", [
    "quantos funcionários foram admitidos em 2026?",
    "quantos dias úteis faltam para acabar o mês?",
    "liste os títulos vencidos há mais de 30 dias",
])
def test_pedidos_simples_nao_viram_intervalo(texto):
    assert coord.metrica_exigida(texto) != "intervalo_entre_datas"


def test_etapa_longa_nao_e_cortada_no_meio():
    frase = ("Vou buscar os funcionários demitidos desde 01/01/2026 em todas as filiais e comparar a data de admissão "
             "com a de demissão de cada um, para contar quem saiu com menos de 90 dias de casa")
    token = andamento._ATUAL.set(("u", "p"))
    try:
        andamento._PASSOS[("u", "p")] = {"passos": [], "t": 0}
        andamento.avisar(frase)
        assert andamento._PASSOS[("u", "p")]["passos"][-1] == frase
    finally:
        andamento._PASSOS.pop(("u", "p"), None)
        andamento._ATUAL.reset(token)


@pytest.mark.django_db
def test_intervalo_sobre_admissoes_vai_para_a_manutencao(settings):
    settings.IATIVA_MANUTENCAO_AUTOMATICA = True
    ac = SimpleNamespace(texto="Quantos admitidos em 2026 saíram antes de completar 90 dias?", exig=coord.CALCULO,
                         trilha=["preparar_consulta:parcial:consulta"], capacidade="")
    inc = coord.encaminhar_manutencao(ac, "falta_capacidade")
    assert inc is not None and inc.condicoes["pergunta"]


def test_contagem_por_intervalo_cumpre_item_de_contagem():
    from iativa.agent.planejamento import conferir
    res = SimpleNamespace(ok=True, medida_calculada="intervalo_entre_datas", linhas=[["Menos de 90 dias", "7"]],
                          colunas=["Cálculo", "Resultado"], totais=None)
    saida = SimpleNamespace(ficha={"tipo": "calculo_resultado"}, observacao=None, resultado=res)
    analise = {"entregas": [{"tipo": "calculo", "metrica": "contagem", "descricao": "Contar quem saiu antes de 90 dias"}]}
    assert conferir(analise, SimpleNamespace(objetivo="", parametros=()), saida) == []


@pytest.mark.django_db
def test_regra_de_intervalo_vai_ao_modelo_desde_a_primeira_decisao(django_user_model):
    from iativa.agent import agente
    u = django_user_model.objects.create_user("m108-notas")
    assert agente.INTERVALO_REGRA in agente._notas(u, "Quantos admitidos em 2026 saíram antes de completar 90 dias?")
    assert agente.INTERVALO_REGRA not in agente._notas(u, "Quantos admitidos em 2026?")
