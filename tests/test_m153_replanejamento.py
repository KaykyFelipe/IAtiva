from types import SimpleNamespace

import pytest

from iativa.agent import revisao
from iativa.agent.diagnostico import Bloco, Diagnostico
from iativa.query.servico import Resultado
from .test_ciclo_agente import base  # noqa: F401


@pytest.mark.parametrize("faltas,esperado", [
    (["Saídas"], {0}), (["Critério geral incorreto"], set()), (["Entradas", "Saídas"], set()),
])
def test_revisao_preserva_apenas_bloco_independente_identificado(faltas, esperado, settings):
    from iativa.agent.provedores import FakeProvider
    titulos = ["Entradas", "Saídas"]
    contrato = [{"descricao": t} for t in titulos]
    resposta = SimpleNamespace(resultado=Diagnostico([
        Bloco(t, Resultado(True, "", colunas=["Quantidade"], linhas=[[1]])) for t in titulos
    ], ""))
    modelo = FakeProvider(revisoes=[{"entregas": [{"descricao": f} for f in faltas]}])
    preservadas = set()
    assert revisao.revisar(None, modelo, "Compare entradas e saídas", contrato, resposta, preservadas=preservadas)
    assert preservadas == esperado

@pytest.mark.django_db
def test_consulta_reprovada_repetida_busca_campos_e_entrega(base):
    from iativa.agent.provedores import FakeProvider
    from iativa.chat import conversas
    from .test_ciclo_agente import _consulta, _d
    u = base["admin"]
    primeira = _consulta(base["cm"])
    corrigida = _d(acao="criar_consulta", parametros=primeira["parametros"], consulta={
        "tabela": "SE1", "nome": "Vencimentos dos títulos", "colunas": ["E1_NUM", "E1_VENCTO"]})
    modelo = FakeProvider([primeira, primeira, corrigida], revisoes=[{
        "entregas": [{"descricao": "Data de vencimento dos títulos"}], "resumo": "Busque o vencimento."}])
    msg, r = conversas.enviar(u, conversas.nova_individual(u), "Liste os títulos com seus vencimentos", modelo)
    assert r is not None and r.ok and r.linhas, msg.texto
    assert any("venc" in c.lower() for c in r.colunas)
    assert not modelo.respostas


@pytest.mark.django_db
@pytest.mark.parametrize("alias,campos,busca,pergunta", [
    ("SA1", ["A1_COD", "A1_NOME"], "Sintético 002", "Comercial: mostre o cadastro do cliente Sintético 002 na filial 01."),
    ("SRA", ["RA_MAT", "RA_NOME"], "BRUNO", "RH: consulte a matrícula e o nome de Bruno na filial 01."),
    ("SE1", ["E1_NUM", "E1_TIPO"], "", "Cobrança: liste o número e o tipo dos títulos da filial 01."),
])
def test_perguntas_novas_com_erp_controlado(base, alias, campos, busca, pergunta):
    import sqlite3
    from django.http import QueryDict
    from iativa.agent import aprendizado
    from iativa.agent.provedores import FakeProvider
    from iativa.chat import conversas
    from .test_ciclo_agente import _d

    u = base["admin"]
    spec = QueryDict(mutable=True)
    spec["nome"] = "Consulta sintética " + alias
    spec.setlist("colunas", campos)
    spec.setlist("setores", [str(base["cob"].pk)])
    cm = aprendizado.aprovar(u, aprendizado.salvar_proposta(u, alias, spec))
    params = [{"codigo": "filial-analise", "valor": "01"}]
    if busca:
        params.append({"codigo": "texto-busca", "valor": busca})
    modelo = FakeProvider([_d(acao="preparar_consulta", objetivo=cm.codigo, parametros=params)])
    m, r = conversas.enviar(u, conversas.nova_individual(u), pergunta, modelo)
    assert r is not None and r.ok and r.linhas, m.texto
    with sqlite3.connect(base["erp"]) as db:
        sql = f"SELECT {','.join(campos)} FROM {alias}010 WHERE D_E_L_E_T_ = ' ' AND {campos[0].split('_')[0]}_FILIAL = ?"
        binds = ["  " if alias == "SA1" else "01"]
        if busca:
            sql += f" AND {campos[1]} LIKE ?"
            binds.append("%" + busca.upper().replace("É", "E") + "%")
        esperado = {tuple(str(c).strip() for c in row) for row in db.execute(sql, binds)}
    assert {tuple(str(c).strip() for c in row[-2:]) for row in r.linhas} == esperado


def test_falha_de_conexao_na_descoberta_nao_vira_ambiguidade(monkeypatch):
    from iativa.agent import aprendizado, planejamento
    tentativas = []
    def falhar(*args, **kwargs):
        tentativas.append(1)
        raise ConnectionError()
    monkeypatch.setattr(aprendizado, "pode_aprender_sozinho", lambda u: True)
    monkeypatch.setattr(aprendizado, "candidatos", falhar)
    with pytest.raises(planejamento.DicionarioIndisponivel):
        planejamento._explorar_dicionario(None, "Quantos equipamentos existem hoje?")
    assert len(tentativas) == 2
