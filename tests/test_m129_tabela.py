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


def test_m130_especificacao_do_modelo_entende_o_jeito_da_pessoa():
    """O modelo lê "separado por filial", "quem entrou e quem saiu", "de julho a setembro"; o backend só valida."""
    analise = {"tabela": {"medidas": [{"evento": "admissão", "rotulo": "Entraram"},
                                      {"evento": "desligamento", "rotulo": "Saíram"}],
                          "periodos": [{"inicio": "2026-07-01", "fim": "2026-07-31", "rotulo": "07/2026"},
                                       {"inicio": "2026-08-01", "fim": "2026-08-31", "rotulo": "08/2026"},
                                       {"inicio": "2026-09-01", "fim": "2026-09-30", "rotulo": "09/2026"}],
                          "agrupar_por": "filial", "intersecao": False}}
    texto = "Quem entrou e quem saiu de julho a setembro, separado por filial?"
    spec = tabela.pedido(texto, analise)
    assert spec["eventos"] == ["admissão", "desligamento"] and spec["por_filial"] and len(spec["periodos"]) == 3
    e = tabela.planejar(texto, {**analise, "entregas": []})["entregas"][0]
    assert {k: v for k, v in e["tabela"].items() if k != "antes"} == spec and "Entraram - 07/2026" in e["descricao"] and "Saíram - 09/2026" in e["descricao"]
    # um evento em 3 meses também é tabela (coluna = só o período)
    um = {"tabela": {**analise["tabela"], "medidas": analise["tabela"]["medidas"][:1]}}
    assert "Tabela por filial: 07/2026, 08/2026, 09/2026" in tabela.planejar(texto, {**um, "entregas": []})["entregas"][0]["descricao"]
    # interseção e datas inválidas não viram tabela
    assert tabela.pedido("x", {"tabela": {**analise["tabela"], "intersecao": True}}) is None
    ruim = {"tabela": {**analise["tabela"], "periodos": [{"inicio": "2026-13-01", "fim": "x", "rotulo": ""}]}}
    assert tabela._do_modelo(ruim["tabela"]) is None


@pytest.mark.django_db
def test_m130_tabela_com_especificacao_do_modelo_ponta_a_ponta(base, erp_sintetico):  # noqa: F811
    q = QueryDict(mutable=True)
    q.update({"nome": "Funcionarios admitidos", "campo_data": "RA_ADMISSA"})
    q.setlist("colunas", ["RA_MAT", "RA_NOME", "RA_ADMISSA", "RA_DEMISSA"])
    q.setlist("setores", [str(base["cob"].pk)])
    aprendizado.aprovar(base["u"], aprendizado.salvar_proposta(base["u"], "SRA", q))
    with sqlite3.connect(erp_sintetico) as conn:
        conn.execute("DELETE FROM SRA010")
        conn.executemany("INSERT INTO SRA010 (RA_FILIAL, RA_MAT, RA_ADMISSA, RA_DEMISSA) VALUES (?, ?, ?, ?)", [
            ("01", "000001", "20260702", "20260915"), ("02", "000001", "20260810", "        ")])
    esp = {"medidas": [{"evento": "admissão", "rotulo": "Entraram"}, {"evento": "desligamento", "rotulo": "Saíram"}],
           "periodos": [{"inicio": f"2026-0{m}-01", "fim": f"2026-0{m}-{d}", "rotulo": f"0{m}/2026"}
                        for m, d in ((7, 31), (8, 31), (9, 30))], "agrupar_por": "filial", "intersecao": False}
    p = FakeProvider([], analises=[{"natureza": "dados", "setores": [base["cob"].codigo], "confianca": 1, "resumo": "",
                                    "memorias": [], "tabela": esp, "entregas": [
                                        {"descricao": "Entradas e saídas", "tipo": "calculo", "metrica": "contagem"}]}])
    msg, r = conversas.enviar(base["u"], conversas.nova_individual(base["u"]),
                              "Quem entrou e quem saiu de julho a setembro, separado por filial?", p)
    assert p.chamadas == 0 and not Incidente.objects.exists(), msg.texto
    assert r.colunas[0] == "Filial" and r.colunas[1] == "Entraram - 07/2026" and r.colunas[-1] == "Saíram - 09/2026"
    linhas = {l[0]: [int(str(v)) for v in l[1:]] for l in r.linhas}
    assert linhas == {rotulo("01"): [1, 0, 0, 0, 0, 1], rotulo("02"): [0, 1, 0, 0, 0, 0]}



def _duas_consultas(base, erp_sintetico):
    q = QueryDict(mutable=True)
    q.update({"nome": "Funcionarios admitidos", "campo_data": "RA_ADMISSA"})
    q.setlist("colunas", ["RA_MAT", "RA_NOME", "RA_ADMISSA", "RA_DEMISSA"])
    q.setlist("setores", [str(base["cob"].pk)])
    aprendizado.aprovar(base["u"], aprendizado.salvar_proposta(base["u"], "SRA", q))
    with sqlite3.connect(erp_sintetico) as conn:
        conn.execute("DELETE FROM SRA010")
        conn.executemany("INSERT INTO SRA010 (RA_FILIAL, RA_MAT, RA_NOME, RA_ADMISSA, RA_DEMISSA) VALUES (?, ?, ?, ?, ?)", [
            ("01", "000001", "ANA TESTE", "20260702", "20260915"), ("02", "000001", "BIA TESTE", "20260810", "        ")])


def _esp(contagem, lista, campos=()):
    return {"medidas": [{"evento": "admissão", "rotulo": "Admitidos"}, {"evento": "desligamento", "rotulo": "Desligados"}],
            "periodos": [{"inicio": "2026-07-01", "fim": "2026-07-31", "rotulo": "07/2026"},
                         {"inicio": "2026-08-01", "fim": "2026-09-30", "rotulo": "08-09/2026"}],
            "agrupar_por": "filial", "intersecao": False, "contagem": contagem,
            "lista": {"pedida": lista, "campos": list(campos)}}


def _enviar(base, esp, texto):
    p = FakeProvider([], analises=[{"natureza": "dados", "setores": [base["cob"].codigo], "confianca": 1, "resumo": "",
                                    "memorias": [], "tabela": esp, "entregas": [
                                        {"descricao": "x", "tipo": "calculo", "metrica": "contagem"}]}])
    msg, r = conversas.enviar(base["u"], conversas.nova_individual(base["u"]), texto, p)
    assert p.chamadas == 0 and not Incidente.objects.exists(), msg.texto
    return r


@pytest.mark.django_db
def test_m131_quantos_e_quais_sao_entrega_lista_primeiro_e_quantidade_por_ultimo(base, erp_sintetico):  # noqa: F811
    _duas_consultas(base, erp_sintetico)
    r = _enviar(base, _esp(True, True), "Quantos foram admitidos e desligados de julho a setembro, por filial, e quais são?")
    assert [b.titulo for b in r.blocos] == ["Quem são", "Quantidade"]
    lista, cont = r.blocos[0].resultado, r.blocos[1].resultado
    assert lista.colunas[:2] == ["Evento", "Período"] and len(lista.linhas) == 3  # 2 admitidos + 1 desligado
    assert cont.colunas[0] == "Filial" and cont.colunas[1] == "Admitidos - 07/2026"


@pytest.mark.django_db
def test_m131_quais_sao_apenas_os_nomes_entrega_so_a_lista_de_nomes(base, erp_sintetico):  # noqa: F811
    _duas_consultas(base, erp_sintetico)
    r = _enviar(base, _esp(False, True, ["nome"]),
                "Quais foram os admitidos e desligados de julho a setembro? Me informe apenas os nomes.")
    assert not hasattr(r, "blocos") or not r.blocos
    assert len(r.colunas) == 1 and "nome" in r.colunas[0].lower()
    assert sorted(l[0].strip() for l in r.linhas) == ["ANA TESTE", "BIA TESTE"]  # sem repetir quem está nos dois


def test_m131_regras_de_reserva_separam_lista_e_quantidade():
    a = tabela.pedido("Quantos foram admitidos e desligados em julho e agosto, e quais são?")
    assert a["contagem"] and a["lista"]["pedida"] and a["lista"]["campos"] == []
    b = tabela.pedido("Quais foram os admitidos e desligados em julho e agosto? Apenas os nomes.")
    assert not b["contagem"] and b["lista"] == {"pedida": True, "campos": ["nome"]}
