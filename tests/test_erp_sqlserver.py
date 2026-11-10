"""Conexão com o ERP real: configuração, cadeia de conexão e verificação do login (sem rede)."""
import pytest

from iativa.metadata import erp_sqlserver as erp

CFG = {
    "host": "10.0.0.1", "porta": "1433", "banco": "HML", "usuario": "leitor", "senha": "s;e}nha",
    "criptografar": "yes", "confiar_certificado": "no",
}


class Conn:
    """Conexão falsa: responde 1 para as consultas/parâmetros em `positivos`."""

    def __init__(self, positivos=()):
        self.positivos = set(positivos)
        self.executados = []

    def cursor(self):
        conn = self

        class Cur:
            def execute(self, sql, params=()):
                conn.executados.append((sql, params))
                self.chave = params[0] if params else sql

            def fetchone(self):
                return (1 if self.chave in conn.positivos else 0,)

            def close(self):
                pass

        return Cur()


def test_sem_configuracao_nao_conecta(settings):
    settings.IATIVA_ERP = None
    with pytest.raises(erp.ErpNaoConfigurado):
        erp.conectar()


def test_cadeia_somente_leitura_e_escapada():
    cadeia = erp._cadeia(CFG)
    assert "ApplicationIntent=ReadOnly" in cadeia and "Encrypt=yes" in cadeia
    assert "PWD={s;e}}nha}" in cadeia  # ; e } não quebram a cadeia


def test_login_somente_leitura():
    assert erp.verificar_login(Conn()) == []


@pytest.mark.parametrize("positivo,problema", [
    ("db_datawriter", "papel db_datawriter"),
    ("db_owner", "papel db_owner"),
    ("INSERT", "permissão INSERT"),
    ("EXECUTE", "permissão EXECUTE"),
    (erp.SQL_SYSADMIN, "papel de servidor sysadmin"),
])
def test_login_com_escrita_e_detectado(positivo, problema):
    assert problema in erp.verificar_login(Conn([positivo]))


def test_verificacoes_sao_consultas_fixas():
    conn = Conn()
    erp.verificar_login(conn)
    assert {sql for sql, _ in conn.executados} == {erp.SQL_SYSADMIN, erp.SQL_PAPEL, erp.SQL_PERMISSAO}
