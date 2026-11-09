"""Gerador determinístico da base sintética do ERP.

Mesma semente => mesmo arquivo lógico. Datas relativas a uma data de referência fixa
(não à data atual) para que os testes sejam reprodutíveis.
"""
import os
import random
import sqlite3
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from . import schema

DEFAULT_SEED = 20260101
REFERENCE_DATE = date(2026, 1, 1)
FILIAIS = ("01", "02", "03")
SEGMENTOS = ("IND", "COM", "SER")
# Ciclo de cenários garante que todos os casos existam em qualquer geração.
TITLE_SCENARIOS = ("aberto", "pago", "parcial", "baixa_cancelada", "excluido", "aberto_vencido")


def _d(value: date) -> str:
    return value.strftime("%Y%m%d")


def _money(rng: random.Random, low: int, high: int) -> Decimal:
    return Decimal(rng.randint(low * 100, high * 100)) / 100


class _Recno:
    def __init__(self):
        self.value = 0

    def next(self) -> int:
        self.value += 1
        return self.value


def _insert(conn, table, row):
    cols = ", ".join(f'"{c}"' for c in row)
    marks = ", ".join("?" for _ in row)
    values = [float(v) if isinstance(v, Decimal) else v for v in row.values()]
    conn.execute(f'INSERT INTO "{table}" ({cols}) VALUES ({marks})', values)


def _populate(conn: sqlite3.Connection, seed: int) -> dict:
    rng = random.Random(seed)
    recno = _Recno()
    counts = {"SA1010": 0, "SE1010": 0, "SE5010": 0}

    # Clientes: cadastro compartilhado (A1_FILIAL em branco); alguns com duas lojas (chave composta).
    clientes = []
    for n in range(1, 13):
        cod = f"{n:06d}"
        lojas = ["01", "02"] if n % 4 == 0 else ["01"]
        for loja in lojas:
            row = {
                "A1_FILIAL": "  ",
                "A1_COD": cod,
                "A1_LOJA": loja,
                "A1_NOME": f"CLIENTE SINTETICO {n:03d} LOJA {loja}",
                "A1_NREDUZ": f"SINT {n:03d}-{loja}",
                "A1_MSBLQL": "1" if n == 7 else "2",
                "A1_XSEGMEN": SEGMENTOS[n % len(SEGMENTOS)],
                "D_E_L_E_T_": " ",
                "R_E_C_N_O_": recno.next(),
                "R_E_C_D_E_L_": 0,
            }
            _insert(conn, "SA1010", row)
            counts["SA1010"] += 1
            clientes.append((cod, loja))

    # Registro excluído logicamente com a mesma chave de um cliente ativo.
    r = recno.next()
    _insert(conn, "SA1010", {
        "A1_FILIAL": "  ", "A1_COD": "000003", "A1_LOJA": "01",
        "A1_NOME": "CLIENTE SINTETICO 003 VERSAO EXCLUIDA", "A1_NREDUZ": "SINT 003-EXC",
        "A1_MSBLQL": "2", "A1_XSEGMEN": "COM", "D_E_L_E_T_": "*", "R_E_C_N_O_": r, "R_E_C_D_E_L_": r,
    })
    counts["SA1010"] += 1

    # Títulos: o mesmo número existe em filiais diferentes; alguns títulos com várias parcelas.
    for i in range(36):
        filial = FILIAIS[i % len(FILIAIS)]
        num = f"{(i // len(FILIAIS)) + 1:09d}"
        parcelas = ["A", "B", "C"] if i % 5 == 0 else [" "]
        cod, loja = clientes[i % len(clientes)]
        for p_idx, parcela in enumerate(parcelas):
            scenario = TITLE_SCENARIOS[(i + p_idx) % len(TITLE_SCENARIOS)]
            emissao = REFERENCE_DATE - timedelta(days=rng.randint(30, 200))
            vencto = emissao + timedelta(days=30 * (p_idx + 1))
            if scenario == "aberto":
                vencto = REFERENCE_DATE + timedelta(days=rng.randint(5, 60))
            vencrea = vencto + timedelta(days=(2 if vencto.weekday() == 5 else 1 if vencto.weekday() == 6 else 0))
            valor = _money(rng, 500, 20000)
            tipo = "NF "
            if i == 13:
                tipo = "RA "
            elif i == 22:
                tipo = "NCC"
            saldo, baixa, movimentos = valor, "", []
            if scenario == "pago":
                saldo, baixa = Decimal("0.00"), _d(vencrea)
                movimentos = [("VL", valor, " ", _d(vencrea))]
            elif scenario == "parcial":
                pago = (valor * Decimal("0.4")).quantize(Decimal("0.01"))
                saldo, baixa = valor - pago, _d(vencrea)
                movimentos = [("VL", pago, " ", _d(vencrea))]
            elif scenario == "baixa_cancelada":
                # Baixa registrada e depois cancelada: saldo volta ao valor integral.
                movimentos = [("VL", valor, "C", _d(vencrea)), ("ES", valor, "C", _d(vencrea + timedelta(days=3)))]
            deleted = scenario == "excluido"
            r = recno.next()
            _insert(conn, "SE1010", {
                "E1_FILIAL": filial, "E1_PREFIXO": "SIN", "E1_NUM": num, "E1_PARCELA": parcela,
                "E1_TIPO": tipo, "E1_CLIENTE": cod, "E1_LOJA": loja,
                "E1_EMISSAO": _d(emissao), "E1_VENCTO": _d(vencto), "E1_VENCREA": _d(vencrea),
                "E1_VALOR": valor, "E1_SALDO": saldo, "E1_BAIXA": baixa,
                "D_E_L_E_T_": "*" if deleted else " ", "R_E_C_N_O_": r, "R_E_C_D_E_L_": r if deleted else 0,
            })
            counts["SE1010"] += 1
            for seq, (tipodoc, mov_valor, situaca, data_mov) in enumerate(movimentos, start=1):
                _insert(conn, "SE5010", {
                    "E5_FILIAL": filial, "E5_DATA": data_mov, "E5_TIPODOC": tipodoc,
                    "E5_PREFIXO": "SIN", "E5_NUMERO": num, "E5_PARCELA": parcela, "E5_TIPO": tipo,
                    "E5_CLIFOR": cod, "E5_LOJA": loja, "E5_SEQ": f"{seq:02d}", "E5_VALOR": mov_valor,
                    "E5_RECPAG": "R", "E5_SITUACA": situaca,
                    "D_E_L_E_T_": " ", "R_E_C_N_O_": recno.next(), "R_E_C_D_E_L_": 0,
                })
                counts["SE5010"] += 1

    for chave, valor in (
        ("origem", "sintetico"),
        ("versao_esquema", schema.SCHEMA_VERSION),
        ("semente", str(seed)),
        ("data_referencia", _d(REFERENCE_DATE)),
    ):
        conn.execute(f'INSERT INTO "{schema.MARKER_TABLE}" ("CHAVE", "VALOR") VALUES (?, ?)', (chave, valor))
    return counts


def generate(path: Path, seed: int = DEFAULT_SEED, replace: bool = False) -> dict:
    """Gera a base sintética em `path` de forma atômica (arquivo temporário + rename)."""
    path = Path(path)
    if path.exists() and not replace:
        raise FileExistsError(str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".erp_sintetico_", suffix=".tmp", dir=path.parent)
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        conn = sqlite3.connect(tmp_path)
        try:
            for statement in schema.ddl():
                conn.execute(statement)
            counts = _populate(conn, seed)
            conn.commit()
        finally:
            conn.close()
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    return counts
