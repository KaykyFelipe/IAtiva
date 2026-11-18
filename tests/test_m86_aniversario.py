"""M86: data de aniversário filtra pelo MÊS, de qualquer ano (aniversariantes do mês)."""
import pytest

from iativa.metadata.catalogo import abrir_catalogo
from iativa.query.compilador import PlanoInvalido, compilar
from iativa.query.plano import Campo, Coluna, Filtro, Plano

pytestmark = pytest.mark.django_db


def _plano(meses):
    return Plano(objetivo='x', base='SRA', colunas=[Coluna('Nome', Campo('e1', 'RA_NOME'))],
                 filtros=[Filtro(Campo('e1', 'RA_FILIAL'), '=', '01'), Filtro(Campo('e1', 'RA_ADMISSA'), 'MES_IN', meses)])


def test_mes_de_qualquer_ano(erp_sintetico, settings):
    settings.IATIVA_METADADOS = {'fonte': 'sintetico'}
    with abrir_catalogo() as cat:
        c = compilar(_plano(('11', '12')), cat, 50)
        assert 'SUBSTRING(e1.RA_ADMISSA, 5, 2) IN (?, ?)' in c.sql and '11' in c.params and '12' in c.params
        with pytest.raises(PlanoInvalido):
            compilar(_plano(("1'; DROP",)), cat, 50)

