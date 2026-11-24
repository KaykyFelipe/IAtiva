"""M142: "por CAMPO" de tabela ligada pelo SX9 junta a tabela sozinho (ex.: vendedor do cabeçalho da nota)."""
import pytest

from iativa.agent.models import ConsultaModelo

from .test_ranking_filiais import _rodar, ana  # noqa: F401 (fixture)

pytestmark = pytest.mark.django_db


def test_agrupa_por_campo_de_tabela_ligada(ana):
    msg, res = _rodar(ana, "soma:E1_VALOR por A1_XSEGMEN")
    assert res is not None and res.ok, msg.texto
    assert res.colunas[0] == "Segmento" and len(res.linhas) >= 1
    cm = ConsultaModelo.objects.get(nome="Titulos ranking")
    assert any(j["tabela"] == "SA1" for j in cm.spec["juncoes"])
    assert any(c["campo"] == "A1_XSEGMEN" and c["alias"] == "j1" for c in cm.spec["colunas"])


def test_campo_sem_ligacao_continua_recusado_com_dica(ana):
    from iativa.agent.aprendizado import ampliar_para_campos, campos_ligados_texto
    _rodar(ana, "soma:E1_VALOR")
    cm = ConsultaModelo.objects.get(nome="Titulos ranking")
    antes = dict(cm.spec)
    assert ampliar_para_campos(ana, cm, ["ZZ9_NADA"]) is False
    cm.refresh_from_db()
    assert cm.spec == antes
    assert "SA1: " in campos_ligados_texto(ana, "SE1") and "A1_XSEGMEN" in campos_ligados_texto(ana, "SE1")
