"""Conexão com o SQL Server do ERP (somente leitura) e verificações de segurança do login.

Camadas de proteção:
1. Login dedicado com permissão só de leitura (garantia principal, conferida por `verificar_login`).
2. `ApplicationIntent=ReadOnly` e `readonly=True` no driver.
3. Só consultas fixas e parametrizadas nesta etapa (metadados).

A string de conexão (com senha) nunca é registrada em log nem exibida.
"""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

DRIVER = "ODBC Driver 18 for SQL Server"
TIMEOUT_S = 10

# Permissões que um login somente leitura NÃO pode ter (nível banco).
PERMISSOES_PROIBIDAS = ("INSERT", "UPDATE", "DELETE", "ALTER", "EXECUTE", "CREATE TABLE", "CONTROL")
PAPEIS_PROIBIDOS = ("db_owner", "db_datawriter", "db_ddladmin", "db_securityadmin")

SQL_AMBIENTE = (
    "SELECT CAST(SERVERPROPERTY('ProductVersion') AS varchar(30)), DB_NAME(), "
    "CAST(DATABASEPROPERTYEX(DB_NAME(), 'Collation') AS varchar(60))"
)
SQL_SYSADMIN = "SELECT IS_SRVROLEMEMBER('sysadmin')"
SQL_PAPEL = "SELECT IS_ROLEMEMBER(?)"
SQL_PERMISSAO = "SELECT HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', ?)"
SQL_TABELAS_SX2 = (
    "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
    "WHERE TABLE_TYPE = 'BASE TABLE' AND TABLE_NAME LIKE 'SX2%' ORDER BY TABLE_NAME"
)
SQL_TABELA_EXISTE = "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = ?"


class ErpNaoConfigurado(ImproperlyConfigured):
    pass


class LoginNaoSomenteLeitura(RuntimeError):
    pass


def _cadeia(cfg: dict) -> str:
    def esc(v: str) -> str:
        return "{" + str(v).replace("}", "}}") + "}"

    return (
        f"DRIVER={{{DRIVER}}};SERVER={esc(cfg['host'] + ',' + cfg['porta'])};DATABASE={esc(cfg['banco'])};"
        f"UID={esc(cfg['usuario'])};PWD={esc(cfg['senha'])};Encrypt={cfg['criptografar']};"
        f"TrustServerCertificate={cfg['confiar_certificado']};ApplicationIntent=ReadOnly;"
        "APP=IAtiva-metadados"
    )


def conectar():
    cfg = getattr(settings, "IATIVA_ERP", None)
    if not cfg:
        raise ErpNaoConfigurado("ERP não configurado (IATIVA_ERP_* no .env do servidor).")
    import pyodbc

    try:
        return pyodbc.connect(_cadeia(cfg), timeout=TIMEOUT_S, readonly=True, autocommit=True)
    except pyodbc.Error as exc:
        # Só o código do erro: a mensagem do driver pode repetir partes da configuração.
        codigo = exc.args[0] if exc.args else "?"
        raise ConnectionError(f"Falha ao conectar no ERP (código {codigo}).") from None


def _um(conn, sql, params=()):
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        row = cur.fetchone()
        return row[0] if row and len(row) == 1 else row
    finally:
        cur.close()


def ambiente(conn) -> dict:
    versao, banco, collation = _um(conn, SQL_AMBIENTE)
    return {"versao": versao, "banco": banco, "collation": collation}


def verificar_login(conn) -> list[str]:
    """Devolve a lista de problemas; vazia = login somente leitura."""
    problemas = []
    if _um(conn, SQL_SYSADMIN) == 1:
        problemas.append("papel de servidor sysadmin")
    for papel in PAPEIS_PROIBIDOS:
        if _um(conn, SQL_PAPEL, (papel,)) == 1:
            problemas.append(f"papel {papel}")
    for perm in PERMISSOES_PROIBIDAS:
        if _um(conn, SQL_PERMISSAO, (perm,)) == 1:
            problemas.append(f"permissão {perm}")
    return problemas


def sufixos_dicionario(conn) -> list[str]:
    cur = conn.cursor()
    try:
        cur.execute(SQL_TABELAS_SX2)
        nomes = [r[0].strip() for r in cur.fetchall()]
    finally:
        cur.close()
    return [n[3:] for n in nomes if len(n) == 6 and n[3:].isdigit()]


def tabela_existe(conn, nome: str) -> bool:
    return bool(_um(conn, SQL_TABELA_EXISTE, (nome,)))
