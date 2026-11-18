"""M104: tabela VAZIA na instalação (ex.: CN9 quando a empresa usa outra rotina) é fonte errada, não resposta zero."""
import sqlite3
from types import SimpleNamespace

import pytest

from iativa.agent import agente
from iativa.metadata.catalogo import abrir_catalogo

pytestmark = pytest.mark.django_db


def test_catalogo_sabe_se_a_tabela_esta_vazia(erp_sintetico, settings):
    settings.IATIVA_METADADOS = {"fonte": "sintetico"}
    with sqlite3.connect(erp_sintetico) as conn:
        conn.execute("CREATE TABLE VAZ010 (X TEXT)")
    with abrir_catalogo() as cat:
        assert cat.vazia("SE1010") is False
        assert cat.vazia("VAZ010") is True
        assert cat.vazia("X; DROP") is None


def test_zero_linhas_de_tabela_vazia_vira_nova_descoberta(monkeypatch):
    monkeypatch.setitem(agente.OBJETIVOS, "aprendido-x", SimpleNamespace(modelo_id=1, tabelas=("CN9",)))

    class Cat:
        def tabelas(self, aliases):
            return {"CN9": SimpleNamespace(fisica="CN9010")}

        def vazia(self, fisica):
            return True

    from contextlib import contextmanager

    @contextmanager
    def abrir():
        yield Cat()
    monkeypatch.setattr("iativa.metadata.catalogo.abrir_catalogo", abrir)
    d = SimpleNamespace(acao="preparar_consulta", objetivo="aprendido-x")
    vazio = SimpleNamespace(resultado=SimpleNamespace(ok=True, linhas=[]))
    assert agente._fonte_vazia(d, vazio) == "CN9"
    com_linhas = SimpleNamespace(resultado=SimpleNamespace(ok=True, linhas=[["1"]]))
    assert agente._fonte_vazia(d, com_linhas) == ""
