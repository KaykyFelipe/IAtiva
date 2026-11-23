"""M129: tabela personalizada com os eventos e períodos pedidos, montada com consultas que a IA já sabe fazer,
sem modelo nem manutenção."""
import sqlite3

import pytest
from django.http import QueryDict

from iativa.accounts.filiais import rotulo
from iativa.agent import aprendizado, tabela
from iativa.agent.provedores import FakeProvider
from iativa.chat import conversas
from iativa.manutencao.models import Incidente

from .test_tarefas import base  # noqa: F401

PEDIDO = "Quantos funcionários foram admitidos e desligados em setembro e outubro, por filial?"


def test_pedido_le_eventos_periodos_e_agrupamento():
    spec = tabela.pedido(PEDIDO)
    assert spec["eventos"] == ["admitidos", "desligados"] and spec["por_filial"]
    assert [r for _, _, r in spec["periodos"]][0].startswith("09/")
    assert tabela.pedido("Quantos funcionários foram admitidos e desligados no mesmo ano, por filial, em 2026?") is None
    assert tabela.pedido("Quantos funcionários foram desligados em setembro e outubro, por filial?") is None
    assert tabela.pedido("Quantos admitidos, transferidos e desligados em setembro e outubro?")["eventos"] == [
        "admitidos", "transferidos", "desligados"]
    assert tabela.pedido("Quantas pessoas foram admitidas e demitidas em 2026?") is None  # composição de contagem.py


def test_contrato_vira_uma_tabela():
    e = tabela.planejar(PEDIDO, {"entregas": [{"descricao": "a", "tipo": "calculo"}, {"descricao": "b", "tipo": "calculo"}]})
    assert len(e["entregas"]) == 1 and e["entregas"][0]["tabela"]
    assert "Admitidos - 09/" in e["entregas"][0]["descricao"] and "Desligados - 10/" in e["entregas"][0]["descricao"]


@pytest.mark.django_db
def test_admitidos_e_desligados_de_dois_meses_numa_tabela_sem_modelo(base, erp_sintetico):  # noqa: F811
    q = QueryDict(mutable=True)
    q.update({"nome": "Funcionarios admitidos", "campo_data": "RA_ADMISSA"})
    q.setlist("colunas", ["RA_MAT", "RA_NOME", "RA_ADMISSA", "RA_DEMISSA"])
    q.setlist("setores", [str(base["cob"].pk)])
    aprendizado.aprovar(base["u"], aprendizado.salvar_proposta(base["u"], "SRA", q))
    from django.utils import timezone
    a = timezone.localdate().year
    with sqlite3.connect(erp_sintetico) as conn:
        conn.execute("DELETE FROM SRA010")
        conn.executemany("INSERT INTO SRA010 (RA_FILIAL, RA_MAT, RA_ADMISSA, RA_DEMISSA) VALUES (?, ?, ?, ?)", [
            ("01", "000001", f"{a}0902", "        "), ("01", "000002", f"{a}1003", f"{a}1020"),
            ("02", "000001", f"{a}0910", f"{a}0925"), ("02", "000002", "20200101", f"{a}1005")])
    p = FakeProvider([], analises=[{"natureza": "dados", "setores": [base["cob"].codigo], "confianca": 1,
                                    "resumo": "", "memorias": [], "entregas": [
                                        {"descricao": "Admitidos por filial", "tipo": "calculo", "metrica": "contagem"},
                                        {"descricao": "Desligados por filial", "tipo": "calculo", "metrica": "contagem"}]}])
    msg, r = conversas.enviar(base["u"], conversas.nova_individual(base["u"]), PEDIDO, p)
    assert p.chamadas == 0 and not Incidente.objects.exists(), msg.texto
    assert r.colunas == ["Filial", f"Admitidos - 09/{a}", f"Admitidos - 10/{a}",
                         f"Desligados - 09/{a}", f"Desligados - 10/{a}"], r.colunas
    linhas = {l[0]: [int(str(v)) for v in l[1:]] for l in r.linhas}
    assert linhas == {rotulo("01"): [1, 1, 0, 1], rotulo("02"): [1, 0, 1, 1]}
    assert r.rodape == ["Total", "2", "1", "1", "2"]
