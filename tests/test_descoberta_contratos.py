"""Descoberta no chat de uma tabela local de itens com um domínio de situação no SX3."""
import os
import sqlite3
import subprocess
import sys

import pytest

from iativa.agent import receitas
from iativa.agent.models import ConsultaModelo
from iativa.chat import conversas
from .test_m95_novos_pedidos import _ambiente, _d, _usuario
from .test_objetivos import Espiao

pytestmark = pytest.mark.django_db(transaction=True)
AGREGACAO = "contagem_distinta:ZZC_ID por ZZC_STATUS categorias A,C,F,E"


@pytest.fixture
def contratos(erp_sintetico, _ambiente):
    from importlib import import_module
    from iativa.memory.models import ParameterDefinition
    # Os testes transaction=True limpam inclusive definições semeadas por migração.
    dados = import_module("iativa.memory.migrations.0004_definicao_agregacao").DADOS
    ParameterDefinition.objects.get_or_create(codigo="agregacao", defaults=dados)
    with sqlite3.connect(erp_sintetico) as db:
        db.execute("ALTER TABLE SX3010 ADD COLUMN X3_CBOX TEXT")
        db.execute("""INSERT INTO SX2010 (X2_CHAVE,X2_ARQUIVO,X2_NOME,X2_MODO,X2_MODOUN,X2_MODOEMP)
                      VALUES ('ZZC','ZZC010','Contratos efetuados por item','E','E','E')""")
        db.execute("""INSERT INTO SIX010 (INDICE,ORDEM,CHAVE) VALUES ('ZZC','1','ZZC_FILIAL+ZZC_ID+ZZC_ITEM')""")
        db.execute("""CREATE TABLE ZZC010 (ZZC_FILIAL TEXT, ZZC_ID TEXT, ZZC_ITEM TEXT, ZZC_DATA TEXT,
                                           ZZC_STATUS TEXT, D_E_L_E_T_ TEXT DEFAULT ' ')""")
        for campo, titulo, tipo, tamanho, caixa in [
            ("ZZC_FILIAL", "Filial", "C", 2, ""), ("ZZC_ID", "Identificador do contrato", "C", 3, ""),
            ("ZZC_ITEM", "Item do contrato", "C", 3, ""), ("ZZC_DATA", "Data da assinatura", "D", 8, ""),
            ("ZZC_STATUS", "Situação atual do contrato", "C", 1, "A=Aprovado;C=Cancelado;F=Fechado;E=Encerrado"),
        ]:
            db.execute("""INSERT INTO SX3010 (X3_ARQUIVO,X3_CAMPO,X3_TITULO,X3_DESCRIC,X3_TIPO,X3_TAMANHO,X3_CBOX)
                          VALUES ('ZZC',?,?,?,?,?,?)""", (campo, titulo, titulo, tipo, tamanho, caixa))
        db.executemany("INSERT INTO ZZC010 VALUES (?,?,?,?,?,?)", [
            ("01", "101", "1", "20261001", "A", " "), ("01", "101", "2", "20261001", "A", " "),
            ("01", "102", "1", "20261031", "F", " "), ("01", "103", "1", "20261016", "E", " "),
            ("01", "104", "1", "20261015", "C", "*"), ("02", "105", "1", "20261015", "C", " "),
            ("01", "106", "1", "20260930", "C", " "),
        ])
    return erp_sintetico


def parametros(mes):
    fim = 31 if mes == 10 else 30
    return [{"codigo": "filial-analise", "valor": "01"},
            {"codigo": "periodo-analise", "valor": f"01/{mes:02}/2026..{fim}/{mes:02}/2026"},
            {"codigo": "agregacao", "valor": AGREGACAO}]


def criar(alias):
    return _d(acao="criar_consulta", entrega="calculo", parametros=parametros(10),
              consulta={"tabela": alias, "nome": "Contratos assinados por situação atual",
                        "colunas": ["ZZC_ID", "ZZC_STATUS"], "campo_data": "ZZC_DATA"})


def test_descobre_apos_fonte_ausente_e_reutiliza_receita(contratos, novo_usuario, politica):
    user = _usuario(novo_usuario, politica, "comercial")
    assert not ConsultaModelo.objects.exists()
    pedido = "Liste a quantidade de contratos que foram efetuados no mês de outubro e separe por status, aprovado, cancelado, fechado e encerrado."
    p = Espiao([criar("ZZX"), _d(acao="aprender_tabela", alvo="contratos efetuados"), criar("ZZC")])
    msg, res = conversas.enviar(user, conversas.nova_individual(user), pedido, p)
    assert res and res.ok, msg.texto
    assert dict(res.linhas) == {"Aprovado": "1", "Cancelado": "0", "Fechado": "1", "Encerrado": "1"}
    assert "01/10/2026" in res.mensagem
    assert any(e.observacoes and "ZZX" in str(e.observacoes) for e in p.entradas)
    assert any(e.tabelas_para_aprender for e in p.entradas)
    cm = ConsultaModelo.objects.get()
    prova = cm.spec["operacao_validada"]
    assert prova["agregacao"] == AGREGACAO
    md = (receitas.pasta() / cm.codigo / "atual.md").read_text(encoding="utf-8")
    assert "ZZC_ITEM" in md and "Encerrado" in md and "20261001" not in md
    # Um novo interpretador Django recebe uma cópia persistida do banco de receitas e o mesmo ERP sintético.
    from django.db import connection
    arquivo = contratos.with_name("catalogo.sqlite3")
    with sqlite3.connect(arquivo) as copia:
        connection.connection.backup(copia)
    inicializar = """
import sys
from pathlib import Path
import django
from django.conf import settings
settings.DATABASES["default"]["NAME"] = sys.argv[1]
settings.SYNTHETIC_ERP_PATH = Path(sys.argv[2])
settings.IATIVA_METADADOS = {"fonte": "sintetico"}
settings.IATIVA_RECEITAS_DIR = Path(sys.argv[3])
django.setup()
from tests.test_descoberta_contratos import conferir_reuso
conferir_reuso()
"""
    subprocess.run([sys.executable, "-c", inicializar, str(arquivo), str(contratos), str(receitas.pasta())],
                   env={**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings.test"}, check=True, timeout=40)


def conferir_reuso():
    from django.contrib.auth import get_user_model
    user = get_user_model().objects.get(username="ti-comercial")
    cm = ConsultaModelo.objects.get()
    # A segunda decisão usa a operação recuperada do banco em vez de recadastrar campos e códigos.
    params = parametros(9)
    params[-1]["valor"] = cm.spec["operacao_validada"]["agregacao"]
    p = Espiao([_d(acao="preparar_consulta", entrega="calculo", objetivo=cm.codigo, parametros=params)])
    msg, res = conversas.enviar(user, conversas.nova_individual(user), "Quantos contratos por situação em setembro?", p)
    assert res and dict(res.linhas) == {"Aprovado": "0", "Cancelado": "1", "Fechado": "0", "Encerrado": "0"}, msg.texto
    assert AGREGACAO in str(p.entradas[0].objetivos)
    assert ConsultaModelo.objects.count() == 1

@pytest.mark.parametrize("problema", ["dois_status", "chave_vazia", "codigo_sem_dominio", "acesso_revogado"])
def test_reexecucao_revalida_dominio_chave_e_autorizacao(contratos, novo_usuario, politica, problema):
    from iativa.agent import orquestrador
    from iativa.query import servico

    user = _usuario(novo_usuario, politica, "comercial")
    msg, res = conversas.enviar(user, conversas.nova_individual(user), "Contar contratos por situação",
                                Espiao([criar("ZZC")]))
    assert res and res.ok, msg.texto
    s = servico.repetir(user, res.solicitacao, conversa=res.solicitacao.conversa)
    assert orquestrador.avaliar(user, s).tipo == "pronta"
    if problema == "acesso_revogado":
        ConsultaModelo.objects.get().setores.clear()
    else:
        with sqlite3.connect(contratos) as db:
            if problema == "dois_status":
                db.execute("UPDATE ZZC010 SET ZZC_STATUS='F' WHERE ZZC_ITEM='2'")
            elif problema == "chave_vazia":
                db.execute("UPDATE ZZC010 SET ZZC_ID=' ' WHERE ZZC_ITEM='2'")
            else:
                db.execute("UPDATE SX3010 SET X3_CBOX='A=Aprovado' WHERE X3_CAMPO='ZZC_STATUS'")
    s.refresh_from_db()
    falha = servico.executar_solicitacao(user, s)
    assert not falha.ok and not falha.linhas
    if problema == "acesso_revogado":
        assert falha.situacao == servico.SEM_PERMISSAO
    else:
        assert falha.situacao in (servico.ERRO_PARAMETROS, servico.ERRO_METADADOS)


def test_classificador_inconclusivo_em_receita_existente_continua_no_mesmo_chat(contratos, novo_usuario, politica):
    from iativa.agent.models import FonteSetorial
    user = _usuario(novo_usuario, politica, "comercial")
    msg, res = conversas.enviar(user, conversas.nova_individual(user), "Quais clientes estão bloqueados?",
                                Espiao([_d(acao="criar_consulta", consulta={
                                    "nome": "Fonte antiga", "tabela": "SA1", "colunas": ["A1_COD"]
                                }, parametros=[{"codigo": "filial-analise", "valor": "01"}])]))
    assert res and res.ok, msg.texto
    antiga = ConsultaModelo.objects.get()
    FonteSetorial.objects.filter(tabela="SA1").delete()

    class FonteIncerta(Espiao):
        def planejar(self, entrada):
            if entrada.planejamento.get("fonte", {}).get("tabela") == "SA1":
                return {"natureza": "ambigua", "confianca": 0.2, "setores": []}
            return super().planejar(entrada)

    p = FonteIncerta([_d(acao="preparar_consulta", objetivo=antiga.codigo, parametros=[
        {"codigo": "filial-analise", "valor": "01"}]),
        _d(acao="conversar", resposta="Não encontrei a fonte."), criar("ZZC")])
    msg, res = conversas.enviar(user, conversas.nova_individual(user), "Conte os contratos por situação de outubro", p)
    assert res and res.ok and "Encerrado" in dict(res.linhas), msg.texto
    assert any(o["situacao"] == "erro_metadados" and "inconclusivo" in o["motivo"]
               for entrada in p.entradas for o in (entrada.observacoes or []))
    assert "20261001" not in str(p.entradas) and "20261031" not in str(p.entradas)


def test_rh_nao_pode_aprender_nem_consultar_fonte_comercial(contratos, novo_usuario, politica):
    from iativa.accounts.models import Department

    setor = Department.objects.get(codigo="rh")
    user = novo_usuario("rh-piloto", setor)
    politica(user, setor, consultar=True, filiais=["01"])

    class SetoresDistintos(Espiao):
        def planejar(self, entrada):
            analise = super().planejar(entrada)
            if entrada.planejamento.get("modo") == "classificar_fonte":
                analise["setores"] = ["comercial"]
            return analise

    msg, res = conversas.enviar(user, conversas.nova_individual(user), "Conte os contratos de outubro",
                                SetoresDistintos([criar("ZZC")]))
    assert res is None and "outro setor" in msg.texto
    assert not ConsultaModelo.objects.exists()


def test_indicacao_de_tabelas_retoma_pergunta_e_executa(contratos, novo_usuario, politica):
    from iativa.chat.models import Mensagem
    user = _usuario(novo_usuario, politica, "comercial")
    conversa = conversas.nova_individual(user)
    pedido = "Quantos contratos de outubro por status, incluindo cancelados?"
    Mensagem.objects.create(conversa=conversa, autor=user, papel="usuario", texto=pedido)
    Mensagem.objects.create(conversa=conversa, papel="assistente", texto="O setor da primeira fonte ficou inconclusivo.")
    p = Espiao([criar("ZZC")])
    msg, res = conversas.enviar(user, conversa, "ZZC e SE1", p)
    assert res and res.ok and dict(res.linhas)["Cancelado"] == "0", msg.texto
    entrada = next(e for e in p.entradas if e.tabelas_para_aprender)
    assert pedido in entrada.mensagem
    assert {t["tabela"] for t in entrada.tabelas_para_aprender} == {"ZZC", "SE1"}


def test_dominio_dinamico_informa_origem_sem_inventar_codigos(contratos):
    from iativa.metadata.catalogo import abrir_catalogo
    with sqlite3.connect(contratos) as db:
        db.execute("UPDATE SX3010 SET X3_CBOX=? WHERE X3_CAMPO='ZZC_STATUS'",
                   ('#IIF(findfunction("LOCSTATUS()"),LOCSTATUS(),"")',))
    with abrir_catalogo() as cat:
        literais, dinamicos = cat.dominios_campos("ZZC")
    assert "ZZC_STATUS" not in literais and dinamicos["ZZC_STATUS"] == "LOCSTATUS"


def test_contagem_dinamica_entrega_codigos_e_mantem_a_legenda_pendente(contratos, novo_usuario, politica):
    with sqlite3.connect(contratos) as db:
        db.execute("UPDATE SX3010 SET X3_CBOX='#LOCSTATUS()' WHERE X3_CAMPO='ZZC_STATUS'")
    user = _usuario(novo_usuario, politica, "comercial")
    decisao = criar("ZZC")
    decisao["parametros"][-1]["valor"] = "contagem_distinta:ZZC_ID por ZZC_STATUS categorias dinamicas"
    msg, res = conversas.enviar(user, conversas.nova_individual(user), "Quantos contratos por status em outubro?",
                                Espiao([decisao]))
    assert res and res.ok and res.situacao == "parcial", msg.texto
    assert dict(res.linhas) == {"A": "1", "E": "1", "F": "1"}
    assert "LOCSTATUS" in res.mensagem and "legenda" in msg.texto
    assert "operacao_validada" not in ConsultaModelo.objects.get().spec


def test_provedor_aceita_periodo_e_agregacao_na_primeira_receita():
    from types import SimpleNamespace
    from iativa.agent.decisao import esquema, validar
    from iativa.agent.provedores import _codigos
    entrada = SimpleNamespace(objetivos=[], tabelas_para_aprender=[{"tabela": "ZZC"}])
    objetivos, parametros = _codigos(entrada)
    permitidos = esquema(objetivos, parametros)["properties"]["parametros"]["items"]["properties"]["codigo"]["enum"]
    assert {"agregacao", "filial-analise", "periodo-analise"} <= set(permitidos)
    assert validar(criar("ZZC"), objetivos, parametros).consulta["tabela"] == "ZZC"


def test_agrupado_por_campo_de_legenda_dinamica_marca_a_legenda_pendente():
    """M105: "contagem:* por STATUS" sem códigos pedidos também reconhece a legenda dinâmica (não repete a consulta)."""
    import inspect
    from iativa.query import servico
    fonte = inspect.getsource(servico._executar_solicitacao)
    assert "not plano.categorias and plano.agrupar and not legenda_pendente" in fonte


def test_legenda_aprendida_nomeia_os_codigos_da_contagem(contratos, novo_usuario, politica):
    """M106: legenda ensinada pela empresa substitui a rotina dinâmica e nomeia cada código."""
    from iativa.agent.legendas import gravar_legenda
    from iativa.metadata.catalogo import abrir_catalogo
    with sqlite3.connect(contratos) as db:
        db.execute("UPDATE SX3010 SET X3_CBOX='#LOCSTATUS()' WHERE X3_CAMPO='ZZC_STATUS'")
    gravar_legenda("ZZC", "ZZC_STATUS", {"A": "Ativo", "E": "Encerrado"})
    gravar_legenda("ZZC", "ZZC_STATUS", {"A": "Aprovado", "E": "Encerrado", "F": "Fechado", "C": "Cancelado"})
    with abrir_catalogo() as cat:
        literais, dinamicos = cat.dominios_campos("ZZC")
    assert "ZZC_STATUS" not in dinamicos and literais["ZZC_STATUS"]["A"] == "Aprovado"
    user = _usuario(novo_usuario, politica, "comercial")
    decisao = criar("ZZC")
    decisao["parametros"][-1]["valor"] = "contagem_distinta:ZZC_ID por ZZC_STATUS"
    msg, res = conversas.enviar(user, conversas.nova_individual(user), "Quantos contratos por status em outubro?",
                                Espiao([decisao]))
    assert res and res.ok, msg.texto
    assert dict(res.linhas) == {"A - Aprovado": "1", "E - Encerrado": "1", "F - Fechado": "1"}
    assert "Me diga a legenda" not in msg.texto
