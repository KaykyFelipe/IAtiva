"""Adaptadores, catálogo e descoberta progressiva com restrição de acesso."""
import sqlite3
from io import StringIO

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.core.management import CommandError, call_command

from iativa.accounts.authz import PermissaoNegada
from iativa.audit.models import OperationAudit
from iativa.metadata import descoberta
from iativa.metadata.catalogo import abrir_catalogo
from iativa.metadata.fontes import DicionarioProtheus, FonteInvalida, InformationSchemaSqlServer, validar_alias
from iativa.metadata.models import DominioSetor, RestricaoMetadado
from iativa.synthetic_erp.connection import open_readonly

pytestmark = pytest.mark.django_db
DOM = "financeiro_receber"


@pytest.fixture
def ana(erp_sintetico, novo_usuario, setores, politica):
    DominioSetor.objects.create(dominio=DOM, setor=setores["cobranca"])
    user = novo_usuario("ana", setores["cobranca"])
    politica(user, setores["cobranca"], consultar=True)
    return user


# --- Adaptadores ----------------------------------------------------------------------------

@pytest.mark.parametrize("alias", ["se1", "SE1; DROP", "SE1010", "", None, "S'1"])
def test_alias_invalido(alias):
    with pytest.raises(FonteInvalida):
        validar_alias(alias)


def test_dicionario_ignora_linhas_excluidas_e_marca_personalizacao(erp_sintetico):
    conn = open_readonly()
    try:
        campos = {c.nome: c for c in DicionarioProtheus(conn).campos("SE1")}
    finally:
        conn.close()
    assert "E1_XEXCLUI" not in campos
    assert campos["E1_XNOMCLI"].virtual and campos["E1_XNOMCLI"].personalizado
    assert not campos["E1_VALOR"].personalizado and campos["E1_VALOR"].tipo == "N"
    assert campos["E1_VENCREA"].tipo == "D"


def test_conexao_continua_somente_leitura(erp_sintetico):
    conn = open_readonly()
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM SX3010")
    finally:
        conn.close()


def test_information_schema_usa_consulta_fixa_parametrizada():
    class Cursor:
        def execute(self, sql, params):
            self.sql, self.params = sql, params

        def fetchall(self):
            return [("E1_NUM   ", "varchar", 9, None, None), ("E1_VALOR", "float", None, 53, None)]

        def close(self):
            pass

    cursor = Cursor()
    conn = type("C", (), {"cursor": lambda self: cursor})()
    cols = InformationSchemaSqlServer(conn).colunas("SE1010")
    assert cursor.sql == InformationSchemaSqlServer.SQL and cursor.params == ("dbo", "SE1010")
    assert cols["E1_NUM"].tipo == "varchar(9)" and cols["E1_VALOR"].tipo == "float(53,0)"
    with pytest.raises(FonteInvalida):
        InformationSchemaSqlServer(conn).colunas("SE1010; DROP TABLE x")


@pytest.mark.parametrize("expr,esperado", [
    ("E5_FILIAL+DTOS(E5_DATA)+E5_BANCO", ("E5_FILIAL", "E5_DATA", "E5_BANCO")),
    ("E1_FILIAL+STR(E1_VALOR,14,2)", ("E1_FILIAL", "E1_VALOR")),
    ("a1_cod+a1_loja", ("A1_COD", "A1_LOJA")),
])
def test_expressao_de_chave_com_funcoes(expr, esperado):
    from iativa.metadata.fontes import _expressao

    assert _expressao(expr) == esperado


@pytest.mark.parametrize("expr", ["", "SUBSTR(X,1,2)", "E1_A+1", "E1_A+IIF(E1_B,E1_C,'')"])
def test_expressao_invalida(expr):
    from iativa.metadata.fontes import _expressao

    with pytest.raises(FonteInvalida):
        _expressao(expr)


@pytest.mark.parametrize("modos,nivel", [
    (("E", "E", "E"), "filial"),
    (("C", "C", "E"), "empresa"),  # ex.: SF4 com F4_FILIAL = "001   "
    (("C", "E", "E"), "unidade"),
    (("C", "C", "C"), "compartilhado"),
    (("C", "", ""), "compartilhado"),
    (("X", "C", "C"), "desconhecido"),
])
def test_nivel_de_compartilhamento(modos, nivel):
    from iativa.metadata.catalogo import nivel_compartilhamento
    from iativa.metadata.tipos import TabelaDicionario

    assert nivel_compartilhamento(TabelaDicionario("SF4", "SF4010", "TES", *modos)) == nivel


# --- Catálogo -------------------------------------------------------------------------------

def test_catalogo_junta_dicionario_e_estrutura_fisica(erp_sintetico):
    with abrir_catalogo() as cat:
        tabelas = cat.tabelas(["SA1", "SE1", "SE5"], com_campos=True)
        rels = cat.relacionamentos(["SA1", "SE1"])
    sa1, se1 = tabelas["SA1"], tabelas["SE1"]
    assert sa1.compartilhamento == "compartilhado" and se1.compartilhamento == "filial"
    assert se1.exclusao_logica and se1.campo_filial == "E1_FILIAL"
    assert se1.chave == ("E1_FILIAL", "E1_PREFIXO", "E1_NUM", "E1_PARCELA", "E1_TIPO")
    campos = {c.nome: c for c in se1.campos}
    assert campos["E1_SALDO"].consultavel
    assert campos["E1_XNOMCLI"].situacao == "virtual" and not campos["E1_XNOMCLI"].consultavel
    assert campos["E1_XANTIGO"].situacao == "sem_coluna_fisica" and not campos["E1_XANTIGO"].consultavel
    assert "D_E_L_E_T_" not in campos
    assert {c.nome for c in sa1.campos if c.personalizado} == {"A1_XSEGMEN"}
    assert [(r.origem, r.destino, r.campos_destino) for r in rels] == [("SA1", "SE1", ("E1_CLIENTE", "E1_LOJA"))]


def test_fonte_real_nao_liberada(settings, erp_sintetico):
    settings.IATIVA_METADADOS = {"fonte": "protheus"}
    with pytest.raises(ImproperlyConfigured):
        with abrir_catalogo():
            pass


# --- Descoberta e acesso --------------------------------------------------------------------

def test_descoberta_progressiva(ana):
    assert [d["codigo"] for d in descoberta.dominios_disponiveis(ana)] == [DOM]
    entidades = descoberta.entidades(ana, DOM)
    assert {t.alias for t in entidades} == {"SA1", "SE1", "SE5"}
    assert all(t.campos == () for t in entidades)  # etapa 2 não traz campos
    (se1,) = descoberta.detalhar(ana, DOM, ["se1"], campos=["E1_SALDO", "E1_VENCREA"])
    assert [c.nome for c in se1.campos] == ["E1_VENCREA", "E1_SALDO"]
    rels = descoberta.relacionamentos(ana, DOM, ["SE1", "SE5"])
    assert rels[0].campos_origem[0] == "E1_FILIAL"


def test_detalhe_limitado_a_poucas_tabelas(ana):
    with pytest.raises(FonteInvalida):
        descoberta.detalhar(ana, DOM, ["SA1", "SE1", "SE5", "SA2"])
    with pytest.raises(FonteInvalida):
        descoberta.detalhar(ana, DOM, [])


def test_tabela_fora_do_dominio_e_negada(ana):
    with pytest.raises(PermissaoNegada):
        descoberta.detalhar(ana, DOM, ["SA2"])


def test_sem_politica_ou_setor_sem_dominio_e_negado(erp_sintetico, novo_usuario, setores, politica):
    DominioSetor.objects.create(dominio=DOM, setor=setores["cobranca"])
    sem_politica = novo_usuario("x", setores["cobranca"])
    vendas = novo_usuario("y", setores["vendas"])
    politica(vendas, setores["vendas"], consultar=True)
    for user in (sem_politica, vendas):
        assert descoberta.dominios_disponiveis(user) == []
        with pytest.raises(PermissaoNegada):
            descoberta.entidades(user, DOM)
    assert OperationAudit.objects.filter(acao="acesso_negado", entidade="metadado").count() == 2


def test_dominio_desativado_bloqueia(ana):
    DominioSetor.objects.update(ativo=False)
    with pytest.raises(PermissaoNegada):
        descoberta.entidades(ana, DOM)


def test_superusuario_sem_politica_nao_descobre(erp_sintetico, novo_usuario, setores):
    DominioSetor.objects.create(dominio=DOM, setor=setores["ti"])
    admin = novo_usuario("admin", setores["ti"], is_superuser=True, is_staff=True)
    with pytest.raises(PermissaoNegada):
        descoberta.entidades(admin, DOM)


def test_restricao_esconde_campo_e_relacionamento(ana, setores):
    RestricaoMetadado.objects.create(tabela="SE1", campo="E1_CLIENTE")
    (se1,) = descoberta.detalhar(ana, DOM, ["SE1"])
    assert "E1_CLIENTE" not in {c.nome for c in se1.campos}
    assert descoberta.relacionamentos(ana, DOM, ["SA1", "SE1"]) == []


def test_restricao_libera_setor_permitido(ana, setores):
    r = RestricaoMetadado.objects.create(tabela="SE1", campo="E1_CLIENTE")
    r.setores_permitidos.add(setores["cobranca"])
    (se1,) = descoberta.detalhar(ana, DOM, ["SE1"])
    assert "E1_CLIENTE" in {c.nome for c in se1.campos}


def test_restricao_de_tabela_inteira(ana):
    RestricaoMetadado.objects.create(tabela="SA1")
    assert {t.alias for t in descoberta.entidades(ana, DOM)} == {"SE1", "SE5"}
    with pytest.raises(PermissaoNegada):
        descoberta.detalhar(ana, DOM, ["SA1"])
    # Documento que cita SA1 deixa de ser recuperado.
    trechos = descoberta.conhecimento(ana, DOM, "segmento do cliente personalizado")
    assert all("segmento" not in t.documento for t in trechos)


@pytest.mark.parametrize("tabela,campo", [("se1", ""), ("SE1", "E1 CLI"), ("TOOLONG", "")])
def test_restricao_valida_nomes(tabela, campo):
    if tabela == "se1":
        assert RestricaoMetadado.objects.create(tabela=tabela, campo=campo).tabela == "SE1"
    else:
        with pytest.raises(Exception):
            RestricaoMetadado.objects.create(tabela=tabela, campo=campo)


# --- Conhecimento na descoberta --------------------------------------------------------------

def test_conhecimento_relevante_e_auditado_sem_termos(ana):
    trechos = descoberta.conhecimento(ana, DOM, "baixa parcial cancelada")
    assert trechos and trechos[0].documento == "procedimentos/baixas-e-cancelamentos.md"
    reg = OperationAudit.objects.get(acao="conhecimento_consultado")
    assert reg.linhas == len(trechos) and reg.estado_novo == "trechos"
    assert not OperationAudit.objects.filter(estado_novo__contains="baixa").exists()


def test_conhecimento_sintetico_nao_vale_para_fonte_real(ana, settings):
    settings.IATIVA_METADADOS = {"fonte": "protheus"}
    assert descoberta.conhecimento(ana, DOM, "baixa parcial") == []


def test_conhecimento_restrito_por_setor(ana, settings, tmp_path):
    settings.IATIVA_CONHECIMENTO_DIR = tmp_path
    base = (
        "---\ntitulo: T\ndominio: financeiro_receber\norigem: sintetico\nversao: 1\nresponsavel: r\n"
        "situacao: aprovado\naprovado_por: r\naprovado_em: 2026-11-10\nsetores: {}\n---\n## Regra\nprazo especial\n"
    )
    (tmp_path / "outro.md").write_text(base.format("vendas"), encoding="utf-8")
    assert descoberta.conhecimento(ana, DOM, "prazo especial") == []
    (tmp_path / "meu.md").write_text(base.format("cobranca"), encoding="utf-8")
    assert [t.documento for t in descoberta.conhecimento(ana, DOM, "prazo especial")] == ["meu.md"]


def test_comando_descobrir(ana):
    saida = StringIO()
    call_command("descobrir_metadados", "ana", "--tabelas", "SE1", "--buscar", "exclusão lógica", stdout=saida)
    texto = saida.getvalue()
    assert "SE1 (SE1010)" in texto and "E1_XNOMCLI" in texto and "(virtual)" in texto
    assert "D_E_L_E_T_" in texto  # trecho de conhecimento
    assert "CLIENTE SINTETICO" not in texto  # nunca dados


def test_comando_descobrir_sem_acesso(erp_sintetico, novo_usuario, setores):
    novo_usuario("z", setores["vendas"])
    with pytest.raises(CommandError):
        call_command("descobrir_metadados", "z", stdout=StringIO())
