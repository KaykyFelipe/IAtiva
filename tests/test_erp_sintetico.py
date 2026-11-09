"""Base sintética do ERP: separada, somente leitura, determinística e com os cenários exigidos."""
import sqlite3

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError, SystemCheckError

from iativa.synthetic_erp import schema
from iativa.synthetic_erp.connection import SyntheticErpError, open_readonly
from iativa.synthetic_erp.generator import generate


def _dump(path):
    conn = sqlite3.connect(path)
    try:
        return list(conn.iterdump())
    finally:
        conn.close()


def test_geracao_deterministica(tmp_path):
    a, b = tmp_path / "a.sqlite3", tmp_path / "b.sqlite3"
    generate(a)
    generate(b)
    assert _dump(a) == _dump(b)


def test_sementes_diferentes_geram_valores_diferentes(tmp_path):
    a, b = tmp_path / "a.sqlite3", tmp_path / "b.sqlite3"
    generate(a, seed=1)
    generate(b, seed=2)
    assert _dump(a) != _dump(b)


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO SA1010 (A1_COD) VALUES ('999999')",
        "UPDATE SE1010 SET E1_SALDO = 0",
        "DELETE FROM SE5010",
        "CREATE TABLE X (a INT)",
        "DROP TABLE SA1010",
    ],
)
def test_conexao_recusa_escrita(erp_sintetico, sql):
    conn = open_readonly()
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute(sql)
    finally:
        conn.close()


def test_recusa_arquivo_que_nao_e_base_sintetica(tmp_path):
    outro = tmp_path / "outro.sqlite3"
    conn = sqlite3.connect(outro)
    conn.execute("CREATE TABLE SA1010 (A1_COD TEXT)")
    conn.commit()
    conn.close()
    with pytest.raises(SyntheticErpError):
        open_readonly(outro)


def test_erro_claro_quando_base_nao_existe(tmp_path):
    with pytest.raises(SyntheticErpError, match="seed_synthetic"):
        open_readonly(tmp_path / "inexistente.sqlite3")


@pytest.fixture
def leitura(erp_sintetico):
    conn = open_readonly()
    yield conn
    conn.close()


def _um(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()[0]


def test_exclusao_logica_presente(leitura):
    assert _um(leitura, "SELECT COUNT(*) FROM SA1010 WHERE D_E_L_E_T_ = '*'") >= 1
    assert _um(leitura, "SELECT COUNT(*) FROM SE1010 WHERE D_E_L_E_T_ = '*'") >= 1
    # Mesma chave de negócio existe ativa e excluída: sem o filtro de exclusão, há duplicidade.
    assert _um(leitura, "SELECT COUNT(*) FROM SA1010 WHERE A1_COD = '000003' AND A1_LOJA = '01'") == 2
    assert _um(leitura, "SELECT COUNT(*) FROM SA1010 WHERE A1_COD = '000003' AND A1_LOJA = '01' AND D_E_L_E_T_ = ' '") == 1


def test_varias_filiais_e_cadastro_compartilhado(leitura):
    filiais = {r[0] for r in leitura.execute("SELECT DISTINCT E1_FILIAL FROM SE1010")}
    assert filiais == {"01", "02", "03"}
    assert {r[0] for r in leitura.execute("SELECT DISTINCT A1_FILIAL FROM SA1010")} == {"  "}
    # O mesmo número de título existe em filiais diferentes.
    assert _um(leitura, "SELECT COUNT(DISTINCT E1_FILIAL) FROM SE1010 WHERE E1_NUM = '000000001'") > 1


def test_chave_composta_cliente_loja(leitura):
    assert _um(leitura, "SELECT COUNT(*) FROM (SELECT A1_COD FROM SA1010 WHERE D_E_L_E_T_ = ' ' "
                        "GROUP BY A1_COD HAVING COUNT(DISTINCT A1_LOJA) > 1)") >= 1
    assert _um(leitura, "SELECT COUNT(*) FROM SE1010 WHERE E1_PARCELA IN ('B', 'C')") >= 1


def test_baixa_parcial_e_cancelada(leitura):
    parciais = _um(leitura, "SELECT COUNT(*) FROM SE1010 WHERE D_E_L_E_T_ = ' ' AND E1_SALDO > 0 AND E1_SALDO < E1_VALOR")
    assert parciais >= 1
    assert _um(leitura, "SELECT COUNT(*) FROM SE5010 WHERE E5_SITUACA = 'C'") >= 1
    assert _um(leitura, "SELECT COUNT(*) FROM SE5010 WHERE E5_TIPODOC = 'ES'") >= 1
    # Agregar SE1 x SE5 sem cuidado duplica valores (título com várias movimentações).
    assert _um(leitura, "SELECT COUNT(*) FROM (SELECT E5_FILIAL, E5_NUMERO, E5_PARCELA FROM SE5010 "
                        "GROUP BY E5_FILIAL, E5_PREFIXO, E5_NUMERO, E5_PARCELA, E5_TIPO HAVING COUNT(*) > 1)") >= 1


def test_tipos_especiais(leitura):
    tipos = {r[0] for r in leitura.execute("SELECT DISTINCT E1_TIPO FROM SE1010")}
    assert {"NF ", "RA ", "NCC"} <= tipos


def test_personalizacao_marcada_explicitamente(leitura):
    campos = schema.TABLES["SA1010"]["campos"]
    personalizados = [nome for nome, info in campos.items() if info.get("personalizado")]
    assert personalizados == ["A1_XSEGMEN"]
    assert _um(leitura, "SELECT COUNT(DISTINCT A1_XSEGMEN) FROM SA1010") > 1


def test_dados_sao_ficticios(leitura):
    nomes = [r[0] for r in leitura.execute("SELECT A1_NOME FROM SA1010")]
    assert nomes and all(n.startswith("CLIENTE SINTETICO") for n in nomes)
    assert _um(leitura, f'SELECT VALOR FROM {schema.MARKER_TABLE} WHERE CHAVE = ?', ("origem",)) == "sintetico"


def test_comando_seed_nao_sobrescreve_sem_flag(settings, tmp_path):
    settings.SYNTHETIC_ERP_PATH = tmp_path / "erp.sqlite3"
    call_command("seed_synthetic")
    with pytest.raises(CommandError, match="--substituir"):
        call_command("seed_synthetic")
    call_command("seed_synthetic", "--substituir")
    open_readonly().close()


def test_comando_seed_nao_toca_o_banco_da_aplicacao(settings, tmp_path, django_db_blocker):
    settings.SYNTHETIC_ERP_PATH = tmp_path / "erp.sqlite3"
    with django_db_blocker.block():  # qualquer acesso ao banco da aplicação falharia
        call_command("seed_synthetic")


@pytest.mark.filterwarnings("ignore:Overriding setting DATABASES")
def test_check_recusa_erp_no_mesmo_arquivo_da_aplicacao(settings, tmp_path):
    settings.ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
    caminho = tmp_path / "mesmo.sqlite3"
    settings.SYNTHETIC_ERP_PATH = caminho
    settings.DATABASES = {**settings.DATABASES, "default": {**settings.DATABASES["default"], "NAME": str(caminho)}}
    with pytest.raises(SystemCheckError, match="iativa.E004"):
        call_command("check")
