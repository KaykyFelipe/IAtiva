"""Esquema da base SINTÉTICA que imita a forma física de tabelas do Protheus.

ATENÇÃO: nomes e convenções seguem padrões comuns do Protheus apenas para exercitar o sistema
(exclusão lógica, filial, chaves compostas, baixas parciais, cancelamentos, personalização).
Nada aqui é referência de regra de negócio real: o comportamento do ambiente real deve ser
validado com a equipe do ERP (Etapa 3). Todos os dados gerados são fictícios.

Campos personalizados são marcados EXPLICITAMENTE em `TABLES` (chave "personalizado"),
e não inferidos pelo prefixo do nome.
"""

MARKER_TABLE = "IATIVA_SINTETICO"
SCHEMA_VERSION = "1"

# Descrição das tabelas: usada pelos testes e, futuramente, pelo adaptador de metadados sintético.
TABLES = {
    "SA1010": {
        "descricao": "Clientes (sintético). Cadastro compartilhado entre filiais (A1_FILIAL em branco).",
        "chave": ["A1_FILIAL", "A1_COD", "A1_LOJA"],
        "campos": {
            "A1_FILIAL": {"tipo": "CHAR(2)"},
            "A1_COD": {"tipo": "CHAR(6)"},
            "A1_LOJA": {"tipo": "CHAR(2)"},
            "A1_NOME": {"tipo": "VARCHAR(40)"},
            "A1_NREDUZ": {"tipo": "VARCHAR(20)"},
            "A1_MSBLQL": {"tipo": "CHAR(1)", "descricao": "'1' bloqueado, '2' liberado"},
            "A1_XSEGMEN": {"tipo": "CHAR(3)", "personalizado": True, "descricao": "Segmento (personalização fictícia)"},
        },
    },
    "SE1010": {
        "descricao": "Títulos a receber (sintético). Exclusivo por filial.",
        "chave": ["E1_FILIAL", "E1_PREFIXO", "E1_NUM", "E1_PARCELA", "E1_TIPO"],
        "campos": {
            "E1_FILIAL": {"tipo": "CHAR(2)"},
            "E1_PREFIXO": {"tipo": "CHAR(3)"},
            "E1_NUM": {"tipo": "CHAR(9)"},
            "E1_PARCELA": {"tipo": "CHAR(1)"},
            "E1_TIPO": {"tipo": "CHAR(3)"},
            "E1_CLIENTE": {"tipo": "CHAR(6)"},
            "E1_LOJA": {"tipo": "CHAR(2)"},
            "E1_EMISSAO": {"tipo": "CHAR(8)", "descricao": "AAAAMMDD"},
            "E1_VENCTO": {"tipo": "CHAR(8)", "descricao": "AAAAMMDD"},
            "E1_VENCREA": {"tipo": "CHAR(8)", "descricao": "AAAAMMDD (vencimento real)"},
            "E1_VALOR": {"tipo": "NUMERIC(14,2)"},
            "E1_SALDO": {"tipo": "NUMERIC(14,2)"},
            "E1_BAIXA": {"tipo": "CHAR(8)", "descricao": "Data da última baixa ou em branco"},
        },
    },
    "SE5010": {
        "descricao": "Movimentação/baixas (sintético).",
        "chave": ["E5_FILIAL", "E5_PREFIXO", "E5_NUMERO", "E5_PARCELA", "E5_TIPO", "E5_SEQ"],
        "campos": {
            "E5_FILIAL": {"tipo": "CHAR(2)"},
            "E5_DATA": {"tipo": "CHAR(8)"},
            "E5_TIPODOC": {"tipo": "CHAR(2)", "descricao": "'VL' baixa; 'ES' estorno"},
            "E5_PREFIXO": {"tipo": "CHAR(3)"},
            "E5_NUMERO": {"tipo": "CHAR(9)"},
            "E5_PARCELA": {"tipo": "CHAR(1)"},
            "E5_TIPO": {"tipo": "CHAR(3)"},
            "E5_CLIFOR": {"tipo": "CHAR(6)"},
            "E5_LOJA": {"tipo": "CHAR(2)"},
            "E5_SEQ": {"tipo": "CHAR(2)"},
            "E5_VALOR": {"tipo": "NUMERIC(14,2)"},
            "E5_RECPAG": {"tipo": "CHAR(1)", "descricao": "'R' receber"},
            "E5_SITUACA": {"tipo": "CHAR(1)", "descricao": "' ' normal; 'C' cancelado"},
        },
    },
}

# Colunas técnicas presentes em todas as tabelas (padrão de exclusão lógica).
TECHNICAL_COLUMNS = {
    "D_E_L_E_T_": "CHAR(1) NOT NULL DEFAULT ' '",
    "R_E_C_N_O_": "INTEGER PRIMARY KEY",
    "R_E_C_D_E_L_": "INTEGER NOT NULL DEFAULT 0",
}


def ddl() -> list[str]:
    statements = []
    for table, spec in TABLES.items():
        cols = [f'"{name}" {info["tipo"]} NOT NULL DEFAULT \'\'' if not info["tipo"].startswith("NUMERIC")
                else f'"{name}" {info["tipo"]} NOT NULL DEFAULT 0'
                for name, info in spec["campos"].items()]
        cols += [f'"{name}" {definition}' for name, definition in TECHNICAL_COLUMNS.items()]
        statements.append(f'CREATE TABLE "{table}" ({", ".join(cols)})')
        key = ", ".join(f'"{c}"' for c in spec["chave"] + ["D_E_L_E_T_", "R_E_C_D_E_L_"])
        statements.append(f'CREATE UNIQUE INDEX "{table}_UNQ" ON "{table}" ({key})')
    statements.append(f'CREATE TABLE "{MARKER_TABLE}" ("CHAVE" TEXT PRIMARY KEY, "VALOR" TEXT NOT NULL)')
    return statements
