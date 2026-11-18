"""Classificação inconclusiva segue para descoberta sem conceder um setor provisório."""
import pytest

from iativa.agent import planejamento
from iativa.agent.models import FonteSetorial

pytestmark = pytest.mark.django_db


def test_classificacao_sem_setor_pede_descoberta(monkeypatch, erp_sintetico, settings, novo_usuario, politica):
    from io import StringIO
    from django.core.management import call_command
    from iativa.accounts.models import Department
    settings.IATIVA_METADADOS = {"fonte": "sintetico"}
    call_command("seed_exemplos", stdout=StringIO())
    ti = Department.objects.get(codigo="ti")
    u = novo_usuario("ti-teste", ti)
    politica(u, ti, consultar=True, filiais=["01"])

    class SemSetor:
        def planejar(self, entrada):
            return {"natureza": "ambigua", "confianca": 0.3, "setores": []}
    with pytest.raises(planejamento.FonteInconclusiva):
        planejamento.autorizar_fonte(u, SemSetor(), "SE5")
    assert not FonteSetorial.objects.filter(tabela="SE5").exists()
