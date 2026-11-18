"""M99: tabela sem setor não trava (herda o setor da ligada ou fica com a TI) e não há "escolha uma tabela"."""
import pytest

from iativa.agent import planejamento
from iativa.agent.models import FonteSetorial

pytestmark = pytest.mark.django_db


def test_fonte_sem_setor_herda_da_tabela_ligada_ou_fica_com_a_ti(monkeypatch):
    FonteSetorial.objects.create(tabela="SA1", assinatura="x", setores=["comercial"], confianca=1)
    monkeypatch.setattr("iativa.knowledge.memoria_tecnica.relacoes",
                        lambda alias: [{"origem": "SA1", "destino": alias}] if alias == "SE1" else [])
    assert planejamento._setores_por_relacao("SE1") == ["comercial"]
    assert planejamento._setores_por_relacao("ZZ9") == []  # sem ligação: quem chama usa ["ti"]
    FonteSetorial.objects.create(tabela="SB1", assinatura="y", setores=["ti"], confianca=0)  # provisória não propaga
    monkeypatch.setattr("iativa.knowledge.memoria_tecnica.relacoes", lambda alias: [{"origem": "SB1", "destino": alias}])
    assert planejamento._setores_por_relacao("SB2") == []


def test_classificacao_sem_setor_vira_provisoria_da_ti(monkeypatch, erp_sintetico, settings, novo_usuario, politica):
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
    planejamento.autorizar_fonte(u, SemSetor(), "SE5")
    f = FonteSetorial.objects.get(tabela="SE5")
    assert f.setores == ["ti"] and f.confianca == 0
