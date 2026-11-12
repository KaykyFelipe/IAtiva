"""Execução somente leitura, com tempo e linhas limitados. Nada é gravado: as linhas voltam em
memória para a requisição e são descartadas em seguida (R3, PLANO §3 princípio 4)."""
import re
import sqlite3
import time
from decimal import Decimal

import sqlglot

# Valores que podem virar literal no SQL Server: só os caracteres que os parâmetros do sistema
# usam (datas, filial, códigos, tipos, padrões de LIKE). Qualquer outro caractere recusa.
LITERAL_SEGURO = re.compile(r"^[0-9A-Za-z %\-]*$")


class TempoEsgotado(RuntimeError):
    pass


class ValorRecusado(ValueError):
    pass


LOCK_TIMEOUT_S = 10  # registro travado no ERP: falha rápido em vez de esperar o tempo todo


def tipo_falha(exc) -> str:
    """"tempo", "conexao", "bloqueio" ou "" (não é falha passageira). Só códigos, nunca o texto do erro
    vai para a tela ou para log (pode conter dados)."""
    if isinstance(exc, TempoEsgotado):
        return "tempo"
    args = getattr(exc, "args", ()) or ()
    estado = str(args[0]) if args else ""
    detalhe = str(args[1]) if len(args) > 1 else ""
    if "1222" in detalhe or "Lock request" in detalhe:
        return "bloqueio"
    if estado.startswith("HYT") or "timeout" in type(exc).__name__.lower():
        return "tempo"
    if estado.startswith("08") or "Communication link" in detalhe:
        return "conexao"
    return ""


def _literal(v) -> str:
    if isinstance(v, bool):
        raise ValorRecusado("Booleano não suportado.")
    if isinstance(v, (int, Decimal)) or (isinstance(v, float) and v == v):
        return str(v)
    if isinstance(v, str) and LITERAL_SEGURO.match(v) and len(v) <= 40:
        return "'" + v + "'"
    raise ValorRecusado("Valor fora do formato permitido.")


def embutir_literais(sql: str, params: tuple) -> str:
    """No SQL Server, consultas parametrizadas sobre as tabelas do Protheus (VARCHAR, collation
    binária) geravam planos que estouravam o tempo; com literais o mesmo SELECT roda na hora.
    Os valores vêm do plano (já validados) e ainda passam por `LITERAL_SEGURO`; o SQL final é
    validado de novo pela árvore sintática antes de executar."""
    partes = sql.split("?")
    if len(partes) != len(params) + 1:
        raise ValorRecusado("Quantidade de parâmetros não confere.")
    out = [partes[0]]
    for valor, resto in zip(params, partes[1:]):
        out += [_literal(valor), resto]
    return "".join(out)


def executar(catalogo, sql_tsql: str, params: tuple, limite: int, timeout_s: int,
             validar_final=None) -> tuple[list[tuple], bool]:
    """Devolve (linhas, truncado). `limite` linhas no máximo. `validar_final(sql)` revalida o SQL
    que de fato vai ao SQL Server (com literais)."""
    conn = catalogo.conn
    if catalogo.dialeto == "sqlite":
        sql = sqlglot.transpile(sql_tsql, read="tsql", write="sqlite")[0]
        prazo = time.monotonic() + timeout_s
        conn.set_progress_handler(lambda: 1 if time.monotonic() > prazo else 0, 10_000)
        try:
            cur = conn.execute(sql, params)
            linhas = cur.fetchmany(limite + 1)
        except sqlite3.OperationalError as exc:
            if "interrupted" in str(exc):
                raise TempoEsgotado("Tempo limite da consulta.") from None
            raise
        finally:
            conn.set_progress_handler(None, 0)
    else:
        sql_final = embutir_literais(sql_tsql, params)
        if validar_final is not None:
            validar_final(sql_final)
        conn.timeout = timeout_s  # pyodbc: tempo máximo por comando
        cur = conn.cursor()
        try:
            # Comando fixo de sessão (não vem do plano): espera no máximo alguns segundos por registro travado.
            cur.execute(f"SET LOCK_TIMEOUT {int(min(LOCK_TIMEOUT_S, timeout_s) * 1000)}")
            cur.execute(sql_final)
            linhas = [tuple(r) for r in cur.fetchmany(limite + 1)]
        finally:
            cur.close()
    return linhas[:limite], len(linhas) > limite
