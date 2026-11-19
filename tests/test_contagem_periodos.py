"""Contagem por filial com dois eventos no período do mesmo vínculo."""
import sqlite3

import pytest

from iativa.accounts.filiais import rotulo
from iativa.agent.models import SituacaoObjetivo
from iativa.agent.provedores import FakeProvider
from iativa.chat import conversas

from .test_tarefas import _d, base  # noqa: F401


@pytest.mark.django_db
@pytest.mark.parametrize("recuperar", [False, True], ids=["direto", "apos_falha_metadados"])
def test_admitidos_e_desligados_no_mesmo_ano_por_filial(base, erp_sintetico, recuperar):
    with sqlite3.connect(erp_sintetico) as conn:
        conn.execute("DELETE FROM SRA010")
        conn.executemany(
            "INSERT INTO SRA010 (RA_FILIAL, RA_MAT, RA_ADMISSA, RA_DEMISSA, D_E_L_E_T_) VALUES (?, ?, ?, ?, ?)",
            [
                ("01", "000001", "20260101", "20260101", " "),
                ("01", "000002", "20261231", "20261231", " "),
                ("02", "000001", "20260301", "20260401", " "),
                ("01", "000003", "20251231", "20260101", " "),
                ("01", "000004", "20261231", "20270101", " "),
                ("02", "000002", "20260101", "        ", " "),
                ("02", "000003", "20260101", "20261231", "*"),
                ("99", "000001", "20260101", "20261231", " "),
                ("02", "000004", "20270101", "20261231", " "),
                ("02", "000005", "20260101", "20251231", " "),
            ],
        )

    def consulta(**params):
        return _d(acao="preparar_consulta", objetivo=base["cm"].codigo,
                  parametros=[{"codigo": k, "valor": v} for k, v in params.items()])

    filtros = {"filial-analise": "TODAS", "periodo-analise": "01/01/2026..31/12/2026"}

    class Roteiro(FakeProvider):
        def decidir(self, entrada):
            # M117: com a exigência certa (cálculo), a orientação dos dois intervalos chega pela regra do sistema
            # nas notas desde a 1ª decisão, além das observações.
            dica_de_recuperacao = recuperar and self.chamadas == 2 and (any(
                "CAMPO@AAAAMMDD..AAAAMMDD" in obs["motivo"]
                for obs in entrada.observacoes
            ) or any("filtro-campo com intervalos" in n for n in entrada.notas))
            if (dica_de_recuperacao or (not recuperar and self.chamadas == 1)) and any(
                "CAMPO@AAAAMMDD..AAAAMMDD" in p["descricao"]
                for o in entrada.objetivos if o["codigo"] == base["cm"].codigo
                for p in o["parametros"] if p["codigo"] == "filtro-campo"
            ):
                self.respostas[0] = consulta(**filtros, agregacao="contagem:* por filial", **{
                    "filtro-campo": "RA_ADMISSA@20260101..20261231; RA_DEMISSA@20260101..20261231",
                })
            return super().decidir(entrada)

    p = Roteiro([
        consulta(**filtros),
        _d(acao="aprender_tabela", alvo="ZZZ"),
        _d(acao="falta_capacidade", capacidade="Filtrar simultaneamente dois campos de data e contar por filial."),
    ], analises=[{
        "natureza": "dados", "setores": [base["cob"].codigo], "confianca": 1,
        "entregas": [{"descricao": "Contagem por filial dos dois eventos em 2026", "tipo": "calculo"}],
    }])
    c = conversas.nova_individual(base["u"])
    msg, res = conversas.enviar(base["u"], c, "Quantos funcionários foram admitidos e desligados no mesmo ano, por filial, em 2026?", p)

    objetivo = c.objetivos.latest("pk")
    assert objetivo.exigencia != "verificacao"  # M117: "no mesmo ano" é recorte de tempo, não vínculo
    if recuperar:
        assert objetivo.trilha[:2] == [
            "preparar_consulta:entrega_pendente:consulta",
            "aprender_tabela:erro_metadados:-",
        ]
    assert objetivo.situacao == SituacaoObjetivo.CUMPRIDO, (objetivo.trilha, msg.texto)
    assert objetivo.melhor_evidencia == "calculo"
    assert res.ok and res.colunas == ["Filial", "Quantidade"]
    assert {filial: int(qtd) for filial, qtd in res.linhas} == {rotulo("01"): 2, rotulo("02"): 1}
    assert res.totais.registros == 3
