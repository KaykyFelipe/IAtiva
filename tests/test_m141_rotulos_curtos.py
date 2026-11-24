"""M141: colunas do resumo lado a lado só com o que diferencia (o mês já está na pergunta)."""
from iativa.agent.contagem import rotulos_curtos


def test_mesmo_mes_fica_so_o_evento():
    assert rotulos_curtos([
        "Contagem de funcionários admitidos em setembro/2026, por filial",
        "Contagem de funcionários desligados em setembro/2026, por filial",
    ]) == ["Admitidos", "Desligados"]


def test_meses_diferentes_levam_o_mes():
    assert rotulos_curtos([
        "Contagem de admitidos em setembro/2026, por filial",
        "Contagem de admitidos em outubro/2026, por filial",
        "Contagem de desligados em setembro/2026, por filial",
        "Contagem de desligados em outubro/2026, por filial",
    ]) == ["Admitidos - Setembro", "Admitidos - Outubro", "Desligados - Setembro", "Desligados - Outubro"]


def test_mesmo_evento_meses_diferentes_fica_so_o_mes():
    assert rotulos_curtos(["Admitidos em março/2026", "Admitidos em abril/2026"]) == ["Março", "Abril"]


def test_sem_diferenca_clara_mantem_os_titulos():
    t = ["Contagem por filial", "Contagem por filial"]
    assert rotulos_curtos(t) == t
