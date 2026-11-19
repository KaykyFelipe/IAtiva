"""Recuperação de parâmetros repetidos na verificação de dois eventos no ano."""
import sqlite3

import pytest

from iativa.accounts.filiais import rotulo
from iativa.agent.models import SituacaoObjetivo
from iativa.agent.provedores import FakeProvider
from iativa.chat import conversas

from .test_tarefas import _d, base  # noqa: F401


@pytest.mark.django_db
def test_contagem_por_filial_recupera_filtro_invalido_repetido(base, erp_sintetico):
    with sqlite3.connect(erp_sintetico) as conn:
        conn.execute("DELETE FROM SRA010")
        conn.executemany(
            "INSERT INTO SRA010 (RA_FILIAL, RA_MAT, RA_ADMISSA, RA_DEMISSA) VALUES (?, ?, ?, ?)",
            [
                ("01", "000001", "20260101", "20260101"),
                ("01", "000002", "20261231", "20261231"),
                ("02", "000001", "20260201", "20260301"),
                ("01", "000003", "20251231", "20260101"),
                ("01", "000004", "20261231", "20270101"),
                ("02", "000002", "20260101", "        "),
                ("99", "000001", "20260101", "20261231"),
            ],
        )

    decisao = _d(acao="preparar_consulta", objetivo=base["cm"].codigo, parametros=[
        {"codigo": "filial-analise", "valor": "TODAS"},
        {"codigo": "periodo-analise", "valor": "01/01/2026..31/12/2026"},
        {"codigo": "agregacao", "valor": "contagem:* por filial"},
        {"codigo": "filtro-campo", "valor": "RA_ADMISSA>=20260101; RA_DEMISSA<=20261231"},
    ])
    provedor = FakeProvider([decisao, decisao], analises=[{
        "natureza": "dados", "setores": [base["cob"].codigo], "confianca": 1,
        "entregas": [{"descricao": "Contagem por filial de admissões e desligamentos no mesmo ano",
                      "tipo": "calculo"}],
    }])
    conversa = conversas.nova_individual(base["u"])
    msg, res = conversas.enviar(
        base["u"], conversa, "Quantos funcionários foram admitidos e desligados no mesmo ano, por filial, em 2026?",
        provedor,
    )

    objetivo = conversa.objetivos.latest("pk")
    assert objetivo.trilha[0] == "preparar_consulta:erro_parametros:-"
    assert objetivo.exigencia != "verificacao"  # M117: "no mesmo ano" é recorte de tempo, não vínculo
    assert objetivo.situacao == SituacaoObjetivo.CUMPRIDO, (objetivo.trilha, msg.texto)
    assert objetivo.melhor_evidencia == "calculo"
    assert res.ok and res.colunas == ["Filial", "Quantidade"]
    assert {filial: int(qtd) for filial, qtd in res.linhas} == {rotulo("01"): 2, rotulo("02"): 1}
    assert res.totais.registros == 3
