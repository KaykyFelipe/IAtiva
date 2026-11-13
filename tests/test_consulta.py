"""Etapa 5: plano, compilação, validação AST, execução somente leitura e regras de negócio."""
import sqlite3
from io import StringIO

import pytest
from django.core.management import call_command

from iativa.accounts.authz import PermissaoNegada
from iativa.accounts.models import AccessPolicy, Department
from iativa.agent import orquestrador as orq
from iativa.agent.provedores import FakeProvider
from iativa.audit.models import OperationAudit
from iativa.chat.estados import TransicaoInvalida
from iativa.chat.models import EstadoSolicitacao as E
from iativa.metadata.catalogo import abrir_catalogo
from iativa.query import servico
from iativa.query.compilador import compilar
from iativa.query.plano import montar
from iativa.query.validador import ConsultaRecusada, validar
from iativa.synthetic_erp.connection import open_readonly

pytestmark = pytest.mark.django_db
PERMITIDAS = {"SE1010", "SA1010"}
BOM = ("SELECT TOP 10 e1.E1_NUM AS c0 FROM SE1010 AS e1 LEFT JOIN SA1010 AS a1 ON a1.A1_FILIAL = ? "
       "AND a1.A1_COD = e1.E1_CLIENTE AND a1.D_E_L_E_T_ = ' ' WHERE e1.D_E_L_E_T_ = ' ' AND e1.E1_FILIAL = ?")


@pytest.fixture(autouse=True)
def _base(erp_sintetico, settings):
    settings.IATIVA_METADADOS = {"fonte": "sintetico"}
    call_command("seed_exemplos", stdout=StringIO())


@pytest.fixture
def ana(novo_usuario, politica):
    setor = Department.objects.get(codigo="financeiro-cobranca")
    user = novo_usuario("ana", setor)
    politica(user, setor, consultar=True, filiais=["01", "02"])
    return user


def _pronta(user, texto):
    r = orq.processar_mensagem(user, texto, provedor=FakeProvider())
    assert r.tipo == "pronta", (r.tipo, r.mensagem, r.erros)
    return r.solicitacao


ATRASO = "inadimplentes corte 2026-01-01, 0 dias, considerar saldo, filial 01"


def _sql_direto(sql, params=()):
    conn = open_readonly()
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


# --- Validador ------------------------------------------------------------------------------

def test_validador_aceita_consulta_correta():
    validar(BOM, PERMITIDAS, 10)


@pytest.mark.parametrize("sql", [
    "DELETE FROM SE1010",
    "SELECT TOP 10 * INTO X FROM SE1010 AS e1 WHERE e1.D_E_L_E_T_ = ' ' AND e1.E1_FILIAL = ?",
    BOM + "; DROP TABLE SE1010",
    BOM.replace("FROM SE1010", "FROM srv.db.dbo.SE1010"),
    BOM.replace("SA1010", "SRA010"),
    BOM.replace("AND a1.D_E_L_E_T_ = ' '", ""),
    BOM.replace("AND e1.E1_FILIAL = ?", ""),
    BOM.replace("TOP 10", "TOP 99999"),
    BOM.replace("SELECT TOP 10", "SELECT"),
    "EXEC xp_cmdshell 'dir'",
    BOM + " UNION SELECT TOP 1 1 FROM SE1010",
    BOM + " AND e1.E1_NUM IN (SELECT TOP 1 E1_NUM FROM SE1010)",
    "SELECT TOP 1 * FROM OPENROWSET('SQLNCLI', 'x', 'SELECT 1') AS e1 WHERE e1.D_E_L_E_T_ = ' ' AND e1.E1_FILIAL = ?",
])
def test_validador_recusa(sql):
    with pytest.raises(ConsultaRecusada):
        validar(sql, PERMITIDAS, 10)


def test_embutir_literais_so_valores_seguros():
    from iativa.query.executor import ValorRecusado, embutir_literais

    sql = embutir_literais("SELECT TOP 2 a FROM T WHERE x = ? AND y > ? AND z NOT LIKE ?", ("001001", 0, "%-"))
    assert sql == "SELECT TOP 2 a FROM T WHERE x = '001001' AND y > 0 AND z NOT LIKE '%-'"
    for ruim in ("a'; DROP TABLE x --", "x'", "a;b", "a" * 41, True, None):
        with pytest.raises(ValorRecusado):
            embutir_literais("SELECT ?", (ruim,))
    with pytest.raises(ValorRecusado):
        embutir_literais("SELECT ?, ?", ("a",))


def test_caminho_sql_server_usa_literais_e_revalida(ana, monkeypatch):
    from iativa.query import executor

    class Cursor:
        def execute(self, sql, *params):
            self.sql, self.params = sql, params

        def fetchmany(self, n):
            return []

        def close(self):
            pass

    cursor = Cursor()
    cat = type("C", (), {"dialeto": "tsql", "conn": type("K", (), {"cursor": lambda self: cursor})()})()
    validados = []
    executor.executar(cat, BOM, ("  ", "001001"), 10, 5, validar_final=validados.append)
    assert cursor.params == () and "'001001'" in cursor.sql and validados == [cursor.sql]


# --- Compilador -----------------------------------------------------------------------------

def test_compilador_usa_nomes_do_catalogo_e_parametros():
    plano = montar("titulos-em-atraso", {"data-corte": "2026-01-01", "dias-tolerancia-atraso": 0,
                                        "tratamento-baixa-parcial": "considerar-saldo", "filial-analise": "01"})
    with abrir_catalogo() as cat:
        comp = compilar(plano, cat, 500)
    assert "SE1010 AS e1" in comp.sql and "SA1010 AS a1" in comp.sql
    assert "2026" not in comp.sql and "'01'" not in comp.sql  # valores só como parâmetro
    assert "  " in comp.params  # SA1 compartilhada: filial em branco
    validar(comp.sql, {"SE1010", "SA1010"}, 501)


def test_campo_virtual_nao_compila():
    from iativa.query.compilador import PlanoInvalido
    from iativa.query.plano import Campo, Coluna

    plano = montar("titulos-a-vencer", {"periodo-analise": {"inicio": "2026-01-01", "fim": "2026-03-31"},
                                       "tratamento-baixa-parcial": "considerar-saldo", "filial-analise": "01"})
    plano.colunas.append(Coluna("x", Campo("e1", "E1_XNOMCLI")))
    with abrir_catalogo() as cat, pytest.raises(PlanoInvalido):
        compilar(plano, cat, 10)


# --- Execução e regras de negócio -----------------------------------------------------------

def test_atraso_bate_com_consulta_de_referencia(ana):
    s = _pronta(ana, ATRASO)
    res = servico.executar_solicitacao(ana, s)
    assert res.ok and res.estado == E.CONCLUIDA, res.mensagem
    # Referência escrita à mão sobre a base sintética.
    ref = _sql_direto(
        "SELECT E1_CLIENTE, E1_LOJA, SUM(E1_SALDO) FROM SE1010 WHERE D_E_L_E_T_=' ' AND E1_FILIAL='01' "
        "AND E1_SALDO>0 AND E1_TIPO NOT IN ('RA ','NCC','PR ') AND E1_TIPO NOT LIKE '%-' AND E1_EMISSAO<='20260101' "
        "AND E1_VENCREA>'19000101' AND E1_VENCREA<'20260101' GROUP BY E1_CLIENTE, E1_LOJA"
    )
    obtido = {(l[0], l[1]): l[4] for l in res.linhas}
    esperado = {(c.strip(), lj.strip()): servico._fmt(v, "dinheiro") for c, lj, v in ref}
    assert obtido == esperado and len(res.linhas) > 0
    assert res.colunas[-1] == "Dias de atraso (maior)"
    assert any("Tipos excluídos" in p for p in res.premissas)


def test_excluidos_e_cancelados_nao_entram(ana):
    s = _pronta(ana, ATRASO)
    res = servico.executar_solicitacao(ana, s)
    nomes = " ".join(l[2] for l in res.linhas)
    assert "VERSAO EXCLUIDA" not in nomes  # SA1 excluída logicamente não duplica cliente
    excluidos = _sql_direto("SELECT COUNT(*) FROM SE1010 WHERE D_E_L_E_T_='*' AND E1_FILIAL='01'")[0][0]
    assert excluidos > 0


def test_baixa_parcial_desconsiderada_reduz_resultado(ana):
    a = servico.executar_solicitacao(ana, _pronta(ana, ATRASO))
    b = servico.executar_solicitacao(ana, _pronta(ana, ATRASO.replace("considerar saldo", "desconsiderar")))
    assert sum(int(l[3]) for l in b.linhas) <= sum(int(l[3]) for l in a.linhas)


def test_a_vencer_e_posicao_cliente(ana):
    r = servico.executar_solicitacao(ana, _pronta(
        ana, "títulos a vencer de 01/01/2026 a 31/03/2026 considerar saldo filial 01"))
    assert r.ok and all(l[0] for l in r.linhas)
    cliente = r.linhas[0][0] if r.linhas else "000001"
    p = servico.executar_solicitacao(ana, _pronta(
        ana, f"posição do cliente {cliente}-01 corte 2026-01-01 considerar saldo filial 01"))
    assert p.ok


def test_resultado_e_sql_nao_sao_gravados(ana, django_db_blocker):
    from django.db import connection

    res = servico.executar_solicitacao(ana, _pronta(ana, ATRASO))
    nome = res.linhas[0][2]
    with connection.cursor() as cursor:
        for tabela in connection.introspection.table_names(cursor):
            cursor.execute(f'SELECT * FROM "{tabela}"')
            conteudo = repr(cursor.fetchall())
            assert nome not in conteudo and "SE1010" not in conteudo
    reg = OperationAudit.objects.get(acao="consulta_executada")
    assert reg.linhas == len(res.linhas) and reg.duracao_ms is not None


def test_so_executa_pronta_e_do_dono(ana, novo_usuario, politica):
    s = _pronta(ana, ATRASO)
    outro = novo_usuario("bia", s.setor)
    politica(outro, s.setor, consultar=True, filiais=["01"])
    with pytest.raises(PermissaoNegada):
        servico.executar_solicitacao(outro, s)
    servico.executar_solicitacao(ana, s)
    with pytest.raises(TransicaoInvalida):
        servico.executar_solicitacao(ana, s)  # concluída é final


def test_perda_de_filial_bloqueia_na_execucao(ana):
    s = _pronta(ana, ATRASO)
    AccessPolicy.objects.filter(user=ana).update(filiais=["02"])
    res = servico.executar_solicitacao(ana, s)
    assert not res.ok and res.estado != E.CONCLUIDA


def test_duplicacao_vira_nao_validada(ana, monkeypatch):
    s = _pronta(ana, ATRASO)
    original = servico.executar

    def duplica(*a, **k):
        linhas, trunc = original(*a, **k)
        return linhas + linhas[:1], trunc

    monkeypatch.setattr(servico, "executar", duplica)
    res = servico.executar_solicitacao(ana, s)
    assert not res.ok and res.estado == E.NAO_VALIDADA


def test_falha_do_banco_vira_falhou(ana, monkeypatch):
    s = _pronta(ana, ATRASO)

    def quebra(*a, **k):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(servico, "executar", quebra)
    res = servico.executar_solicitacao(ana, s)
    assert res.estado == E.FALHOU and "disk" not in res.mensagem


def test_repetir_cria_nova_com_mesmos_valores(ana):
    s = _pronta(ana, ATRASO)
    servico.executar_solicitacao(ana, s)
    nova = servico.repetir(ana, s)
    r = orq.avaliar(ana, nova)
    assert r.tipo == "pronta" and nova.pk != s.pk


def test_executar_pelo_chat_salva_resultado_no_historico_m44(client, ana):
    from django.urls import reverse

    from iativa.chat.models import ConversationContext, Mensagem

    client.force_login(ana)
    html = client.post(reverse("chat:nova"), {"mensagem": ATRASO}, HTTP_HX_REQUEST="true").content.decode()
    assert "<table>" in html and "R$" in html and "Critérios e premissas" in html  # executou direto
    assert "CLIENTE SINTETICO" in html
    c = ConversationContext.objects.get()
    assert "CLIENTE SINTETICO" not in repr(list(Mensagem.objects.values_list("texto", "ficha")))
    # M44: a tabela original permanece e executar de novo continua disponível para atualizar.
    html = client.get(reverse("chat:conversa", args=[c.pk])).content.decode()
    assert "CLIENTE SINTETICO" in html and "Executar de novo" in html
    assert Mensagem.objects.exclude(resultado={}).exclude(resultado=None).exists()
    s = c.solicitacoes.get()
    html = client.post(reverse("chat:executar_de_novo", args=[c.pk, s.pk]), HTTP_HX_REQUEST="true").content.decode()
    assert "CLIENTE SINTETICO" in html and c.solicitacoes.count() == 2

def test_executar_sem_pronta_avisa(client, ana):
    from django.urls import reverse

    from iativa.chat.models import ConversationContext

    client.force_login(ana)
    client.post(reverse("chat:nova"), {"mensagem": "inadimplentes"})
    c = ConversationContext.objects.get()
    html = client.post(reverse("chat:executar", args=[c.pk]), HTTP_HX_REQUEST="true").content.decode()
    assert "Não há consulta pronta" in html


def test_limite_de_linhas(ana, settings):
    settings.IATIVA_CONSULTA = {"limite_linhas": 2, "timeout_s": 30}
    res = servico.executar_solicitacao(ana, _pronta(ana, ATRASO))
    assert len(res.linhas) <= 2 and (res.truncado or len(res.linhas) < 2)
