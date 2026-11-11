"""Nomes das filiais: reconhecer pelo nome e mostrar ao lado do código."""
import pytest

from iativa.accounts.filiais import codigo_por_texto, rotulo
from iativa.agent.conversao import converter
from iativa.memory.models import TipoValor


@pytest.mark.parametrize("texto,esperado", [
    ("Cuiabá", "001002"), ("cuiaba", "001002"), ("filial de Ribeirão Preto", "001008"),
    ("na filial Campo Grande", "001005"), ("001004", "001004"), ("001004 · Limeira", "001004"),
    ("Araçatuba.", "001006"), ("Matriz", "001001"), ("Curitiba", None), ("009999", None), ("", None),
])
def test_codigo_por_texto(texto, esperado):
    assert codigo_por_texto(texto) == esperado


def test_rotulo():
    assert rotulo("001007") == "001007 · Londrina"
    assert rotulo("01") == "01"


def test_converter_filial_por_nome():
    class D:
        tipo = TipoValor.FILIAL

    assert converter(D, "Uberlândia") == "001003"
    assert converter(D, "01") == "01"
