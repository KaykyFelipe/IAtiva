"""M53: a IA declara a capacidade que falta; vai na hora para a manutenção, com o que implementar."""
import pytest

from iativa.agent.decisao import DecisaoInvalida, validar
from iativa.agent.agente import _sem_narracao


def test_falta_capacidade_exige_descricao():
    with pytest.raises(DecisaoInvalida):
        validar({"acao": "falta_capacidade", "resposta": ""}, [], [])
    d = validar({"acao": "falta_capacidade", "resposta": "", "capacidade": "operação de projeção linear"}, [], [])
    assert d.capacidade == "operação de projeção linear"


def test_resposta_final_sem_narracao():
    t = ("Vou levantar o faturamento realizado de 01/11/2026 a 16/11/2026.\n\nConsulta executada às 10:21: 8 linha(s)."
         "\n\nVou consultar o faturamento realizado neste mês.")
    assert _sem_narracao(t) == "Consulta executada às 10:21: 8 linha(s)."


@pytest.mark.django_db
def test_capacidade_declarada_vai_para_manutencao_com_o_que_implementar():
    from iativa.agent import objetivos as coord
    from iativa.manutencao.models import Categoria

    ac = coord.Acompanhamento("faça uma projeção do faturamento", exig=coord.CALCULO)
    ac.capacidade = "operação de projeção linear do mês"
    ac.anotar("falta_capacidade", "declarada")
    inc = coord.encaminhar_manutencao(ac, "capacidade_declarada")
    assert inc.categoria == Categoria.CAPACIDADE and inc.situacao == "aberto"
    assert inc.condicoes["capacidade"] == "operação de projeção linear do mês"
    outro = coord.Acompanhamento("x", exig=coord.CALCULO)
    outro.capacidade = "feriados estaduais"
    outro.anotar("falta_capacidade", "declarada")
    assert coord.encaminhar_manutencao(outro, "capacidade_declarada").pk != inc.pk
