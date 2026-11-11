from datetime import date

import pytest

from iativa.agent.periodo import periodo_da_mensagem

HOJE = date(2026, 11, 11)


@pytest.mark.parametrize("texto,esperado", [
    ("faça a consulta de novo agora desde janeiro", "01/01/2026..11/11/2026"),
    ("procure desde agosto se tem da alcans", "01/08/2026..11/11/2026"),
    ("desde dezembro de 2025", "01/12/2025..11/11/2026"),
    ("mês passado foi lançada uma conta da Dell?", "01/10/2026..31/10/2026"),
    ("em março", "01/03/2026..31/03/2026"),
    ("neste mês", "01/11/2026..11/11/2026"),
    ("de 01/02/2026 a 15/02/2026", "01/02/2026..15/02/2026"),
    ("a partir de 10/09/2026", "10/09/2026..11/11/2026"),
    ("e da dell?", ""),
])
def test_periodo_da_mensagem(texto, esperado):
    assert periodo_da_mensagem(texto, HOJE) == esperado


def test_desde_mes_futuro_e_do_ano_passado():
    assert periodo_da_mensagem("desde dezembro", date(2026, 3, 5)) == "01/12/2025..05/03/2026"
