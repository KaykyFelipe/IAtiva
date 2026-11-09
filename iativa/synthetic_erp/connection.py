"""Conexão SOMENTE LEITURA com a base sintética do ERP.

A aplicação nunca escreve na base do ERP. Aqui isso é garantido em duas camadas:
abertura em modo `ro` (URI SQLite) e `PRAGMA query_only`. Para o SQL Server real, a garantia
principal será o login com permissão mínima de leitura (Etapa 5).
"""
import sqlite3
from pathlib import Path

from django.conf import settings

from . import schema


class SyntheticErpError(RuntimeError):
    pass


def open_readonly(path: Path | None = None) -> sqlite3.Connection:
    path = Path(path or settings.SYNTHETIC_ERP_PATH)
    if not path.is_file():
        raise SyntheticErpError(
            "Base sintética do ERP não encontrada. Gere com: python manage.py seed_synthetic"
        )
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA query_only = ON")
        row = conn.execute(
            f'SELECT "VALOR" FROM "{schema.MARKER_TABLE}" WHERE "CHAVE" = ?', ("origem",)
        ).fetchone()
    except sqlite3.DatabaseError as exc:
        conn.close()
        raise SyntheticErpError("O arquivo informado não é uma base sintética do IAtiva.") from exc
    if not row or row[0] != "sintetico":
        conn.close()
        raise SyntheticErpError("O arquivo informado não é uma base sintética do IAtiva.")
    return conn
