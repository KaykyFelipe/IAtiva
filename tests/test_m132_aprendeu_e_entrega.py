"""M132: a IA aprendeu a consulta e a pessoa não citou período ("pedidos em aberto", "itens de nota da filial 01").
A consulta aprendida roda com todo o histórico até hoje e a resposta chega, sem parar numa pergunta escondida."""
import pytest

from iativa.agent.models import ConsultaModelo
from iativa.chat import conversas

from . import test_calculo_apos_aprender as base_teste
from .test_ciclo_agente import base  # noqa: F401  (fixture base)
from .test_diagnostico import faturamento  # noqa: F401  (fixture com SD2 sintética)

pytestmark = pytest.mark.django_db


def test_aprendeu_sem_periodo_ja_entrega_o_calculo(faturamento, monkeypatch):  # noqa: F811
    from iativa.accounts.models import AccessPolicy, Department
    AccessPolicy.objects.create(user=faturamento["ana"], department=Department.objects.get(codigo="ti"),
                                pode_administrar=True)
    monkeypatch.setattr(base_teste, "CRITERIOS", [{"codigo": "filial-analise", "valor": "01"}])
    c = conversas.nova_individual(faturamento["ana"])
    msg, res = conversas.enviar(faturamento["ana"], c, "some o total dos itens de nota da filial 01",
                                base_teste.Roteiro([]))
    assert ConsultaModelo.objects.filter(tabela="SD2").exists()
    assert res is not None and res.ok, msg.texto
    assert any("Soma" in col for col in res.colunas), msg.texto
