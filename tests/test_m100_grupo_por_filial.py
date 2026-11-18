"""M100: "por CAMPO por filial": o grupo fica separado por filial (não soma as filiais)."""
import pytest

from iativa.agent.provedores import FakeProvider
from iativa.chat import conversas
from iativa.query.plano import ler_agregacao, ler_por_filial

from .test_ciclo_agente import _d, base  # noqa: F401

pytestmark = pytest.mark.django_db
SPEC = {"colunas": [{"campo": "E1_CLIENTE", "tipo": "texto", "rotulo": "Cliente"}]}


def test_parser_por_campo_e_por_filial():
    assert ler_agregacao("contagem:* por E1_CLIENTE por filial", SPEC)[1] == "E1_CLIENTE"
    assert ler_agregacao("contagem:* por filial por E1_CLIENTE", SPEC)[1] == "E1_CLIENTE"
    assert ler_por_filial("contagem:* por E1_CLIENTE por E1_FILIAL") and ler_por_filial("contagem:* por filial por E1_CLIENTE")
    assert not ler_por_filial("contagem:* por E1_CLIENTE") and not ler_por_filial("contagem:* por filial")


def test_execucao_mantem_a_filial_em_cada_grupo(base):
    b = base
    cliente = next(c["campo"] for c in b["cm"].spec["colunas"] if "CLIENTE" in c["campo"])
    params = [{"codigo": "filial-analise", "valor": "TODAS"},
              {"codigo": "agregacao", "valor": f"contagem:* por {cliente} por filial"}]
    if b["cm"].spec.get("campo_data"):
        params.append({"codigo": "periodo-analise", "valor": "01/01/2000..31/12/2030"})
    p = FakeProvider([_d(acao="preparar_consulta", objetivo=b["cm"].codigo, entrega="calculo", parametros=params)])
    m, r = conversas.enviar(b["admin"], conversas.nova_individual(b["admin"]), "quantos títulos por cliente e por filial?", p)
    assert r is not None and r.ok, m.texto
    assert "Filial" in r.colunas
    filiais = {l[r.colunas.index("Filial")] for l in r.linhas}
    assert len(filiais) > 1  # grupos de filiais diferentes não foram somados numa linha só
