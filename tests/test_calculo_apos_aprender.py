"""Pedido de cálculo que exige criar a consulta antes: depois de aprender a tabela (SD2), a consulta nova
precisa aparecer para o modelo no passo seguinte, senão só resta repetir aprender_tabela (acao_repetida)."""
import pytest

from iativa.agent.models import ConsultaModelo, Objetivo
from iativa.chat import conversas

from .test_ciclo_agente import Espiao, _d, base  # noqa: F401  (fixture base)
from .test_diagnostico import faturamento  # noqa: F401  (fixture com SD2 sintética)

pytestmark = pytest.mark.django_db

ITENS = {"tabela": "SD2", "nome": "Itens de nota", "colunas": ["D2_TES", "D2_TOTAL", "D2_EMISSAO"],
         "campo_data": "D2_EMISSAO"}
CRITERIOS = [{"codigo": "filial-analise", "valor": "01"},
             {"codigo": "periodo-analise", "valor": "01/09/2026..30/09/2026"}]


class Roteiro(Espiao):
    """Passo 1: aprender a tabela. Passo 2: se a consulta aprendida estiver no catálogo, pede a soma nela;
    se não estiver, o único caminho visível é aprender de novo (a trilha do incidente)."""

    def decidir(self, entrada):
        self.entradas.append(entrada)
        self.chamadas += 1
        aprender = _d(acao="aprender_tabela", alvo="SD2 itens de nota", entrega="calculo", consulta=ITENS,
                      parametros=CRITERIOS)
        if self.chamadas == 1:
            return aprender
        cm = ConsultaModelo.objects.filter(tabela="SD2").first()
        if cm is not None and cm.codigo in {o["codigo"] for o in entrada.objetivos}:
            return _d(acao="preparar_consulta", objetivo=cm.codigo, entrega="calculo",
                      parametros=CRITERIOS + [{"codigo": "agregacao", "valor": "soma:D2_TOTAL"}])
        return aprender


def test_calculo_usa_consulta_recem_aprendida(faturamento):  # noqa: F811
    c = conversas.nova_individual(faturamento["ana"])
    p = Roteiro([])
    msg, res = conversas.enviar(faturamento["ana"], c, "some o total dos itens de nota da filial 01 em setembro", p)
    assert ConsultaModelo.objects.filter(tabela="SD2").exists()
    o = Objetivo.objects.filter(conversa=c).first()
    assert o is None or "acao_repetida" not in o.motivo, (o.trilha, o.motivo)
    assert res is not None and res.ok, msg.texto
    assert any("Soma" in col for col in res.colunas), msg.texto
    assert "ainda não foi feito" not in msg.texto
