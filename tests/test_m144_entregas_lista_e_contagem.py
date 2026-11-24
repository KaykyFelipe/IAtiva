"""M144: listas nominais com colunas juntadas de título genérico e pedidos mistos (contagem + lista)."""
import sqlite3

import pytest
from django.http import QueryDict

from iativa.agent import aprendizado, entregas, planejamento
from iativa.agent.models import Objetivo
from iativa.agent.objetivos import Acompanhamento
from iativa.agent.provedores import FakeProvider
from iativa.chat import conversas
from iativa.query.servico import Resultado

from .test_ciclo_agente import _d, base  # noqa: F401

pytestmark = pytest.mark.django_db


def _adicionar_srj(erp_path):
    with sqlite3.connect(erp_path) as conn:
        conn.execute(
            "INSERT INTO SX2010 (X2_CHAVE, X2_ARQUIVO, X2_NOME, X2_MODO, X2_MODOUN, X2_MODOEMP) "
            "VALUES ('SRJ', 'SRJ010', 'Funções', 'C', 'C', 'C')"
        )
        conn.executemany(
            "INSERT INTO SX3010 (X3_ARQUIVO, X3_ORDEM, X3_CAMPO, X3_TIPO, X3_TAMANHO, X3_DECIMAL, X3_TITULO, X3_CONTEXT) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'R')",
            [
                ("SRJ", "01", "RJ_FILIAL", "C", "2", "0", "Filial"),
                ("SRJ", "02", "RJ_FUNCAO", "C", "5", "0", "Função"),
                ("SRJ", "03", "RJ_DESC", "C", "30", "0", "Descricao"),
                ("SRA", "11", "RA_NOMECMP", "C", "40", "0", "Nome complet"),
            ],
        )
        conn.execute(
            "INSERT INTO SIX010 (INDICE, ORDEM, CHAVE, DESCRICAO) VALUES ('SRJ', '1', 'RJ_FILIAL+RJ_FUNCAO', 'Codigo')"
        )
        conn.execute(
            "INSERT INTO SX9010 (X9_DOM, X9_IDENT, X9_CDOM, X9_EXPDOM, X9_EXPCDOM) "
            "VALUES ('SRJ', '001', 'SRA', 'RJ_FUNCAO', 'RA_CODFUNC')"
        )
        cols_sra = {r[1] for r in conn.execute("PRAGMA table_info(SRA010)").fetchall()}
        if "RA_NOMECMP" not in cols_sra:
            conn.execute("ALTER TABLE SRA010 ADD COLUMN RA_NOMECMP TEXT DEFAULT 'PESSOA SINTETICA UM'")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS SRJ010 (RJ_FILIAL TEXT, RJ_FUNCAO TEXT, RJ_DESC TEXT, "
            "D_E_L_E_T_ CHAR(1) DEFAULT ' ', R_E_C_N_O_ INTEGER PRIMARY KEY)"
        )
        conn.execute("DELETE FROM SRJ010")
        conn.executemany(
            "INSERT INTO SRJ010 (RJ_FILIAL, RJ_FUNCAO, RJ_DESC) VALUES (?, ?, ?)",
            [("  ", "00001", "ANALISTA SINTETICO"), ("  ", "00002", "TECNICO SINTETICO")],
        )
        conn.execute("UPDATE SRA010 SET RA_CODFUNC = '00001', RA_NOMECMP = 'PESSOA SINTETICA UM' WHERE RA_FILIAL = '01'")
        conn.execute("UPDATE SRA010 SET RA_CODFUNC = '00002', RA_NOMECMP = 'PESSOA SINTETICA DOIS' WHERE RA_FILIAL = '02'")


def _cm_ativos(b, com_juncao_salva=True):
    q = QueryDict(mutable=True)
    q.update({"nome": "Funcionários ativos sintéticos"})
    q.setlist("colunas", ["RA_MAT", "RA_NOMECMP", "RA_CODFUNC", "RA_SITFOLH"])
    q.setlist("setores", [str(b["cob"].pk)])
    cm = aprendizado.aprovar(b["admin"], aprendizado.salvar_proposta(b["admin"], "SRA", q))
    spec = dict(cm.spec)
    spec["filtros_fixos"] = [{"campo": "RA_SITFOLH", "op": "<>", "valor": "D"}]
    if com_juncao_salva:
        # Simula consulta já gravada antes do M144, em que SRJ.RJ_DESC tinha rótulo bruto "Descricao".
        spec["juncoes"] = [{"alias": "j1", "tabela": "SRJ", "pares": [["RJ_FUNCAO", "RA_CODFUNC"]]}]
        spec["colunas"] = list(spec["colunas"]) + [
            {"campo": "RJ_DESC", "rotulo": "Descricao", "tipo": "texto", "alias": "j1"}
        ]
    cm.spec = spec
    cm.save(update_fields=["spec"])
    return cm


def test_campo_tecnico_comprova_funcao_mesmo_com_rotulo_descricao():
    assert entregas.faltam([{"campo": "RJ_DESC", "rotulo": "Descricao"}], ["funcao"]) == []
    assert entregas.faltam([{"campo": "Q3_DESCSUM", "rotulo": "Descricao"}], ["funcao"]) == []
    assert entregas.faltam([{"campo": "RA_CODFUNC", "rotulo": "Cod. Funcao"}], ["funcao"]) == ["funcao"]


def test_acompanhamento_nomes_aceita_juncao_srj_e_qualifica_rotulo(base):
    b = base
    _adicionar_srj(b["erp"])
    cm = _cm_ativos(b, com_juncao_salva=True)
    analise = {
        "natureza": "dados",
        "setores": [b["cob"].codigo],
        "confianca": 0.99,
        "resumo": "",
        "memorias": [],
        "entregas": [
            {
                "tipo": "consulta",
                "metrica": "",
                "descricao": "Nomes dos funcionários ativos hoje, com função e filial",
                "campos_pedidos": ["nome", "funcao", "filial"],
            }
        ],
    }
    p = FakeProvider(
        [
            _d(
                acao="preparar_consulta",
                objetivo=cm.codigo,
                entrega="consulta",
                parametros=[{"codigo": "filial-analise", "valor": "TODAS"}],
            )
        ],
        analises=[analise],
    )
    c = conversas.nova_individual(b["ana"])
    m, r = conversas.enviar(b["ana"], c, "me informe agora com os nomes das pessoas", p)
    assert r is not None and r.ok, m.texto
    assert "Descricao (Funções)" in r.colunas
    assert not entregas.faltam(r.colunas_meta, ["nome", "funcao", "filial"])
    assert p.chamadas == 1


def test_consulta_sem_juncao_amplia_sozinha_quando_contrato_pede_funcao(base):
    b = base
    _adicionar_srj(b["erp"])
    cm = _cm_ativos(b, com_juncao_salva=False)
    analise = {
        "natureza": "dados",
        "setores": [b["cob"].codigo],
        "confianca": 0.99,
        "resumo": "",
        "memorias": [],
        "entregas": [
            {
                "tipo": "consulta",
                "metrica": "",
                "descricao": "Lista dos ativos com nome e função",
                "campos_pedidos": ["nome", "funcao"],
            }
        ],
    }
    p = FakeProvider(
        [
            _d(
                acao="preparar_consulta",
                objetivo=cm.codigo,
                entrega="consulta",
                parametros=[{"codigo": "filial-analise", "valor": "TODAS"}],
            )
        ],
        analises=[analise],
    )
    c = conversas.nova_individual(b["ana"])
    m, r = conversas.enviar(b["ana"], c, "me diga os nomes das pessoas e a função", p)
    cm.refresh_from_db()
    assert any(col["campo"] == "RJ_DESC" for col in cm.spec["colunas"])
    assert r is not None and r.ok, m.texto
    assert p.chamadas == 1


def test_pedido_misto_contagem_e_lista_entrega_ambos_mesmo_sem_atende(base):
    b = base
    _adicionar_srj(b["erp"])
    cm = _cm_ativos(b, com_juncao_salva=True)
    analise = {
        "natureza": "dados",
        "setores": [b["cob"].codigo],
        "confianca": 0.99,
        "resumo": "",
        "memorias": [],
        "entregas": [
            {"tipo": "calculo", "metrica": "contagem", "descricao": "Contagem de funcionários ativos hoje, por filial"},
            {
                "tipo": "consulta",
                "metrica": "",
                "descricao": "Lista de nomes completos e função atual dos ativos hoje, organizada por filial",
                "campos_pedidos": ["nome", "funcao", "filial"],
            },
        ],
    }
    p = FakeProvider(
        [
            _d(
                acao="preparar_consulta",
                objetivo=cm.codigo,
                entrega="consulta",
                atende=[],
                parametros=[{"codigo": "filial-analise", "valor": "TODAS"}],
            ),
            _d(
                acao="preparar_consulta",
                objetivo=cm.codigo,
                entrega="calculo",
                atende=[],
                parametros=[
                    {"codigo": "filial-analise", "valor": "TODAS"},
                    {"codigo": "agregacao", "valor": "contagem:*"},
                ],
            ),
        ],
        analises=[analise],
    )
    c = conversas.nova_individual(b["ana"])
    m, r = conversas.enviar(
        b["ana"],
        c,
        "Quantos funcionários temos ativos hoje, em cada filial, me diga os nomes das pessoas e a função",
        p,
    )
    o = Objetivo.objects.filter(conversa=c).order_by("-pk").first()
    assert r is not None and getattr(r, "consolidado", False), m.texto
    assert len(r.blocos) == 2
    assert o is not None and o.situacao == "cumprido"
    assert sorted(o.contrato["cumpridas"]) == [0, 1]


def test_duas_listas_distintas_sem_atende_nao_sao_marcadas_pela_mesma_tabela():
    from iativa.agent.agente import Saida
    from iativa.agent.decisao import validar

    ac = Acompanhamento(
        "Quais entraram e quais saíram?",
        contrato=[
            {"tipo": "consulta", "metrica": "", "descricao": "Lista A"},
            {"tipo": "consulta", "metrica": "", "descricao": "Lista B"},
        ],
    )
    d = validar(_d(acao="preparar_consulta", atende=[]), [], [])
    r = Resultado(True, "ok", colunas=["Nome"], tipos=["texto"], linhas=[["X"]])
    planejamento.registrar_entregas(ac, d, Saida("", resultado=r))
    assert ac.cumpridas == []
