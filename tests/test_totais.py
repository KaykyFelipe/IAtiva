"""Cálculos corretos (M22): total completo no banco, subtotal, nulos, zero, junção, filiais e médias.

A referência de cada teste vem direto do ERP sintético (SQLite), fora do sistema.
"""
import sqlite3
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command
from django.http import QueryDict

from iativa.accounts.models import Department
from iativa.agent import aprendizado
from iativa.agent.provedores import FakeProvider
from iativa.chat import conversas
from iativa.query import servico

pytestmark = pytest.mark.django_db

PERIODO = "01/01/2000..31/12/2035"


@pytest.fixture
def admin(erp_sintetico, settings, novo_usuario, politica):
    settings.IATIVA_METADADOS = {"fonte": "sintetico"}
    settings.IATIVA_APRENDIZADO_AUTOAPROVACAO = True
    call_command("seed_exemplos", stdout=StringIO())
    cob = Department.objects.get(codigo="financeiro-cobranca")
    u = novo_usuario("diego", cob)
    politica(u, cob, consultar=True, administrar=True, aprovar_conhecimento=True, filiais=["01", "02"])
    return u


def _sql(erp, sql, params=()):
    with sqlite3.connect(erp) as conn:
        return conn.execute(sql, params).fetchall()


def _ref(erp, filiais=("01",), func="SUM"):
    marcas = ",".join("?" for _ in filiais)
    (v, n), = _sql(erp, f'SELECT {func}(E1_VALOR), COUNT(*) FROM SE1010 WHERE E1_FILIAL IN ({marcas}) '
                        "AND D_E_L_E_T_ = ' '", filiais)
    return (Decimal(str(v)).quantize(Decimal("0.01")) if v is not None else None), n


def _consulta(admin, nome="Titulos", colunas=("E1_NUM", "E1_CLIENTE", "E1_VALOR"), campo_data=""):
    q = QueryDict(mutable=True)
    q.update({"nome": nome, "campo_data": campo_data})
    q.setlist("colunas", list(colunas))
    q.setlist("setores", [str(Department.objects.get(codigo="financeiro-cobranca").pk)])
    return aprendizado.aprovar(admin, aprendizado.salvar_proposta(admin, "SE1", q))


def _d(**kw):
    base = {"resposta": "", "acao": "conversar", "objetivo": "", "parametros": [], "alvo": "", "nota": "",
            "confirmado": False, "opcoes": []}
    return {**base, **kw}


def _pedir(admin, cm, filial="01", agregacao="", texto="x", conversa=None):
    c = conversa or conversas.nova_individual(admin)
    params = [{"codigo": "filial-analise", "valor": filial}]
    if agregacao:
        params.append({"codigo": "agregacao", "valor": agregacao})
    return conversas.enviar(admin, c, texto, FakeProvider([_d(acao="preparar_consulta", objetivo=cm.codigo,
                                                             parametros=params)]))


def test_soma_correta_na_primeira_interacao(admin, erp_sintetico):
    cm = _consulta(admin)
    soma, n = _ref(erp_sintetico)
    msg, res = _pedir(admin, cm, agregacao="soma:E1_VALOR", texto="some os títulos da filial 01")
    assert res.ok and res.situacao == servico.SUCESSO, msg.texto
    medida = res.totais.medidas[0]
    assert medida.funcao == "SUM" and medida.valor.quantize(Decimal("0.01")) == soma and res.totais.registros == n
    # A resposta final (transitória) já traz o total e a quantidade, sem precisar perguntar "quanto deu?".
    assert medida.texto in res.mensagem and f"{n} registro" in res.mensagem


def test_total_completo_com_mais_registros_que_o_limite(admin, erp_sintetico, settings):
    settings.IATIVA_CONSULTA = {"limite_linhas": 5, "timeout_s": 30}
    cm = _consulta(admin)
    soma, n = _ref(erp_sintetico)
    assert n > 5
    msg, res = _pedir(admin, cm)
    assert res.ok and res.truncado and len(res.linhas) == 5 and res.situacao == servico.PARCIAL
    assert res.totais.registros == n and res.totais.medidas[0].valor.quantize(Decimal("0.01")) == soma
    assert f"ao todo são {n}" in res.mensagem
    # Pedido de total com a lista cortada: a agregação cobre todos os registros.
    msg, res = _pedir(admin, cm, agregacao="soma:E1_VALOR")
    assert res.totais.medidas[0].valor.quantize(Decimal("0.01")) == soma and res.totais.registros == n


def test_distingue_total_completo_de_subtotal(admin, erp_sintetico, settings):
    """Objetivo fixo com coluna de dinheiro e lista cortada: subtotal das linhas ≠ total completo."""
    settings.IATIVA_CONSULTA = {"limite_linhas": 3, "timeout_s": 30}
    c = conversas.nova_individual(admin)
    p = FakeProvider([_d(acao="preparar_consulta", objetivo="titulos-a-vencer", parametros=[
        {"codigo": "filial-analise", "valor": "01"}, {"codigo": "periodo-analise", "valor": PERIODO},
        {"codigo": "tratamento-baixa-parcial", "valor": "considerar-saldo"}])])
    msg, res = conversas.enviar(admin, c, "títulos a vencer", p)
    assert res.ok and res.truncado, msg.texto
    assert res.subtotal and res.total and res.subtotal != res.total
    assert "Subtotal das linhas exibidas" in res.mensagem and "Total completo" in res.mensagem
    assert res.total_valor > sum(Decimal(l[-1].replace("R$ ", "").replace(".", "").replace(",", "."))
                                 for l in res.linhas)


def test_sem_registros_nao_e_zero(admin, erp_sintetico):
    cm = _consulta(admin, campo_data="E1_EMISSAO")
    c = conversas.nova_individual(admin)
    p = FakeProvider([_d(acao="preparar_consulta", objetivo=cm.codigo, parametros=[
        {"codigo": "filial-analise", "valor": "01"}, {"codigo": "periodo-analise", "valor": "01/01/1990..31/12/1990"},
        {"codigo": "agregacao", "valor": "soma:E1_VALOR"}])])
    msg, res = conversas.enviar(admin, c, "some 1990", p)
    assert res.ok and res.situacao == servico.VAZIO and res.linhas == [] and res.totais.registros == 0
    assert "Nenhum registro" in res.mensagem and "diferente de um total igual a zero" in res.mensagem


def test_soma_zero_e_valores_nulos(admin, erp_sintetico):
    cm = _consulta(admin)
    _sql(erp_sintetico, "UPDATE SE1010 SET E1_VALOR = 0 WHERE E1_FILIAL = '01'")
    msg, res = _pedir(admin, cm, agregacao="soma:E1_VALOR")
    assert res.ok and res.totais.registros > 0 and res.totais.medidas[0].valor == 0
    assert "0,00" in res.mensagem and "Nenhum registro" not in res.mensagem

    # SUM = NULL com registros (campo vazio em todos): o banco sintético não aceita NULL nessa coluna,
    # então o caso é montado direto no acumulador, como a consulta agregada devolveria.
    from iativa.query.plano import Campo, Plano

    ac = servico._Acumulador([("Soma de Vlr.Título", "SUM", "valor", Campo("e1", "E1_VALOR"))])
    ac.juntar("", 7, [(None, 0)])
    totais = ac.totais()
    assert totais.registros == 7 and totais.medidas[0].valor is None
    texto = servico._resposta_final("aprendido-x", Plano(objetivo="aprendido-x"), 1, False, totais, "", "", True, 1)
    assert "sem valor" in texto and "não é zero" in texto and "Nenhum registro" not in texto


def test_media_entre_filiais_e_ponderada(admin, erp_sintetico):
    cm = _consulta(admin)
    soma, n = _ref(erp_sintetico, ("01", "02"))
    media = (soma / n).quantize(Decimal("0.01"))
    m1, _ = _ref(erp_sintetico, ("01",), "AVG")
    m2, _ = _ref(erp_sintetico, ("02",), "AVG")
    msg, res = _pedir(admin, cm, filial="TODAS", agregacao="media:E1_VALOR, soma:E1_VALOR")
    assert res.ok and len(res.linhas) == 2 and res.colunas[0] == "Filial", msg.texto
    med, som = res.totais.medidas
    assert med.funcao == "AVG" and med.valor.quantize(Decimal("0.01")) == media
    assert som.valor.quantize(Decimal("0.01")) == soma and res.totais.registros == n
    assert media != ((m1 + m2) / 2).quantize(Decimal("0.01")) or m1 == m2  # não é média de médias


def test_ranking_nao_vira_total_geral(admin, erp_sintetico):
    cm = _consulta(admin)
    soma, n = _ref(erp_sintetico)
    msg, res = _pedir(admin, cm, agregacao="soma:E1_VALOR por E1_CLIENTE top 2")
    assert res.ok and len(res.linhas) == 2
    assert res.totais.medidas[0].valor.quantize(Decimal("0.01")) == soma and res.totais.registros == n
    assert "Ranking" in res.mensagem and "não só os do ranking" in res.mensagem


def _com_juncao(admin):
    c = conversas.nova_individual(admin)
    p = FakeProvider([
        _d(acao="aprender_tabela", alvo="SE1 titulos"),
        _d(acao="criar_consulta", parametros=[{"codigo": "filial-analise", "valor": "01"}],
           consulta={"tabela": "SE1", "nome": "Titulos com nome", "colunas": ["E1_NUM", "E1_CLIENTE", "E1_VALOR"],
                     "juncoes": [{"tabela": "SA1", "colunas": ["A1_NOME"]}]}),
    ])
    msg, res = conversas.enviar(admin, c, "títulos com o nome do cliente", p)
    assert res is not None and res.ok, msg.texto
    from iativa.agent.models import ConsultaModelo

    return ConsultaModelo.objects.get(nome="Titulos com nome")


def test_agregacao_com_juncao_nao_infla(admin, erp_sintetico):
    cm = _com_juncao(admin)
    soma, n = _ref(erp_sintetico)
    msg, res = _pedir(admin, cm, agregacao="soma:E1_VALOR por A1_NOME")
    assert res.ok, msg.texto
    assert res.totais.medidas[0].valor.quantize(Decimal("0.01")) == soma and res.totais.registros == n


def test_juncao_que_duplica_e_recusada_antes_de_somar(admin, erp_sintetico):
    cm = _com_juncao(admin)
    # Cadastro com a chave repetida: cada título desse cliente apareceria duas vezes na junção.
    _sql(erp_sintetico, 'INSERT INTO SA1010 (A1_FILIAL, A1_COD, A1_LOJA, A1_NOME, A1_NREDUZ, A1_MSBLQL, A1_XSEGMEN, '
                        '"D_E_L_E_T_", "R_E_C_N_O_", "R_E_C_D_E_L_") '
                        "SELECT A1_FILIAL, A1_COD, A1_LOJA, 'DUPLICADO', A1_NREDUZ, A1_MSBLQL, A1_XSEGMEN, ' ', "
                        "900000 + R_E_C_N_O_, 1 FROM SA1010 WHERE A1_COD = '000001'")
    msg, res = _pedir(admin, cm, agregacao="soma:E1_VALOR por A1_NOME")
    assert res is None and "inflaria os valores" in msg.texto
    assert msg.solicitacao.estado == "nao_validada"


def test_numero_nao_fica_no_historico_nem_na_auditoria(admin, erp_sintetico):
    from iativa.audit.models import OperationAudit
    from iativa.chat.models import Mensagem

    cm = _consulta(admin)
    msg, res = _pedir(admin, cm, agregacao="soma:E1_VALOR", texto="some os títulos")
    total = res.totais.medidas[0].texto
    assert total in res.mensagem  # a pessoa vê o número
    for m in Mensagem.objects.all():
        assert total not in m.texto and total not in str(m.ficha)
    for e in OperationAudit.objects.all():
        assert total not in str(e.__dict__)
    # Ao reabrir: critérios e botão de nova execução, sem valor antigo.
    registro = conversas.mensagens_com_interacao(admin, msg.conversa)[-1]
    assert registro["repetivel"] and registro["m"].ficha["criterios"]
