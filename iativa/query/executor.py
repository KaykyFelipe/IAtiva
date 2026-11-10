"""Execução somente leitura, com tempo e linhas limitados. Nada é gravado: as linhas voltam em
memória para a requisição e são descartadas em seguida (R3, PLANO §3 princípio 4)."""
import sqlite3
import time

import sqlglot


class TempoEsgotado(RuntimeError):
    pass


def executar(catalogo, sql_tsql: str, params: tuple, limite: int, timeout_s: int) -> tuple[list[tuple], bool]:
    """Devolve (linhas, truncado). `limite` linhas no máximo."""
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
        conn.timeout = timeout_s  # pyodbc: tempo máximo por comando
        cur = conn.cursor()
        try:
            cur.execute(sql_tsql, params)
            linhas = [tuple(r) for r in cur.fetchmany(limite + 1)]
        finally:
            cur.close()
    return linhas[:limite], len(linhas) > limite
