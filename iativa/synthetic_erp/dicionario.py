"""Dicionário SINTÉTICO no formato das tabelas SX do Protheus (SX2, SX3, SIX, SX9).

Serve para exercitar o adaptador de dicionário antes do acesso ao ambiente real. As colunas
usadas são um subconjunto comum dessas tabelas; a forma de acesso ao dicionário real ainda
precisa ser confirmada com a equipe do ERP (informação 4 do STATUS.md).

Tudo aqui é derivado de `schema.TABLES`, mais alguns casos de teste:
- um campo virtual (`E1_XNOMCLI`), que existe só no dicionário e não pode ser consultado;
- um campo do dicionário sem coluna física (`E1_XANTIGO`), que deve aparecer como inconsistente;
- uma linha excluída logicamente no SX3, que deve ser ignorada.
"""
from . import schema

SUFIXO = "010"
DICT_TABLES = {
    "SX2": ["X2_CHAVE", "X2_ARQUIVO", "X2_NOME", "X2_MODO"],
    "SX3": [
        "X3_ARQUIVO", "X3_ORDEM", "X3_CAMPO", "X3_TIPO", "X3_TAMANHO", "X3_DECIMAL",
        "X3_TITULO", "X3_DESCRIC", "X3_PROPRI", "X3_CONTEXT",
    ],
    "SIX": ["INDICE", "ORDEM", "CHAVE", "DESCRICAO"],
    "SX9": ["X9_DOM", "X9_IDENT", "X9_CDOM", "X9_EXPDOM", "X9_EXPCDOM"],
}

NOMES_TABELAS = {"SA1": "Clientes", "SE1": "Contas a Receber", "SE5": "Movimentação Bancária"}
# 'C' compartilhado, 'E' exclusivo (por filial). Fictício: o real vem da informação 5.
MODOS = {"SA1": "C", "SE1": "E", "SE5": "E"}
CAMPOS_DATA = {"E1_EMISSAO", "E1_VENCTO", "E1_VENCREA", "E1_BAIXA", "E5_DATA"}
TITULOS = {
    "A1_FILIAL": "Filial", "A1_COD": "Código", "A1_LOJA": "Loja", "A1_NOME": "Nome",
    "A1_NREDUZ": "N Fantasia", "A1_MSBLQL": "Bloqueado", "A1_XSEGMEN": "Segmento",
    "E1_FILIAL": "Filial", "E1_PREFIXO": "Prefixo", "E1_NUM": "No. Título", "E1_PARCELA": "Parcela",
    "E1_TIPO": "Tipo", "E1_CLIENTE": "Cliente", "E1_LOJA": "Loja", "E1_EMISSAO": "DT Emissão",
    "E1_VENCTO": "Vencimento", "E1_VENCREA": "Vencto Real", "E1_VALOR": "Vlr.Título",
    "E1_SALDO": "Saldo", "E1_BAIXA": "DT Baixa",
    "E5_FILIAL": "Filial", "E5_DATA": "DT Movimento", "E5_TIPODOC": "Tipo Doc", "E5_PREFIXO": "Prefixo",
    "E5_NUMERO": "Número", "E5_PARCELA": "Parcela", "E5_TIPO": "Tipo", "E5_CLIFOR": "Cli/For",
    "E5_LOJA": "Loja", "E5_SEQ": "Sequência", "E5_VALOR": "Valor", "E5_RECPAG": "Rec/Pag",
    "E5_SITUACA": "Situação",
}
# (domínio, contradomínio, expressão no domínio, expressão no contradomínio)
RELACIONAMENTOS = [
    ("SA1", "SE1", "A1_COD+A1_LOJA", "E1_CLIENTE+E1_LOJA"),
    ("SE1", "SE5", "E1_FILIAL+E1_PREFIXO+E1_NUM+E1_PARCELA+E1_TIPO",
     "E5_FILIAL+E5_PREFIXO+E5_NUMERO+E5_PARCELA+E5_TIPO"),
]


def nome_fisico(alias: str) -> str:
    return f"{alias}{SUFIXO}"


def ddl() -> list[str]:
    out = []
    for tabela, colunas in DICT_TABLES.items():
        cols = [f'"{c}" TEXT NOT NULL DEFAULT \'\'' for c in colunas]
        cols += ['"D_E_L_E_T_" CHAR(1) NOT NULL DEFAULT \' \'', '"R_E_C_N_O_" INTEGER PRIMARY KEY']
        out.append(f'CREATE TABLE "{nome_fisico(tabela)}" ({", ".join(cols)})')
    return out


def _tipo_x3(campo: str, tipo_fisico: str) -> tuple[str, int, int]:
    if tipo_fisico.startswith("NUMERIC"):
        tam, dec = tipo_fisico[len("NUMERIC("):-1].split(",")
        return "N", int(tam), int(dec)
    tamanho = int(tipo_fisico[tipo_fisico.index("(") + 1:-1])
    return ("D" if campo in CAMPOS_DATA else "C"), tamanho, 0


def linhas() -> dict[str, list[dict]]:
    rows: dict[str, list[dict]] = {t: [] for t in DICT_TABLES}
    for fisica, spec in schema.TABLES.items():
        alias = fisica[:3]
        rows["SX2"].append({
            "X2_CHAVE": alias, "X2_ARQUIVO": fisica, "X2_NOME": NOMES_TABELAS[alias], "X2_MODO": MODOS[alias],
        })
        for ordem, (campo, info) in enumerate(spec["campos"].items(), start=1):
            tipo, tamanho, decimal = _tipo_x3(campo, info["tipo"])
            rows["SX3"].append({
                "X3_ARQUIVO": alias, "X3_ORDEM": f"{ordem:02d}", "X3_CAMPO": campo, "X3_TIPO": tipo,
                "X3_TAMANHO": str(tamanho), "X3_DECIMAL": str(decimal), "X3_TITULO": TITULOS[campo],
                "X3_DESCRIC": info.get("descricao", TITULOS[campo]),
                "X3_PROPRI": "U" if info.get("personalizado") else "S", "X3_CONTEXT": "R",
            })
        rows["SIX"].append({
            "INDICE": alias, "ORDEM": "1", "CHAVE": "+".join(spec["chave"]), "DESCRICAO": "Chave principal",
        })
    rows["SX3"] += [
        {"X3_ARQUIVO": "SE1", "X3_ORDEM": "90", "X3_CAMPO": "E1_XNOMCLI", "X3_TIPO": "C", "X3_TAMANHO": "40",
         "X3_DECIMAL": "0", "X3_TITULO": "Nome Cliente", "X3_DESCRIC": "Nome do cliente (virtual, fictício)",
         "X3_PROPRI": "U", "X3_CONTEXT": "V"},
        {"X3_ARQUIVO": "SE1", "X3_ORDEM": "91", "X3_CAMPO": "E1_XANTIGO", "X3_TIPO": "C", "X3_TAMANHO": "10",
         "X3_DECIMAL": "0", "X3_TITULO": "Campo antigo", "X3_DESCRIC": "Sem coluna física (fictício)",
         "X3_PROPRI": "U", "X3_CONTEXT": "R"},
        {"X3_ARQUIVO": "SE1", "X3_ORDEM": "92", "X3_CAMPO": "E1_XEXCLUI", "X3_TIPO": "C", "X3_TAMANHO": "1",
         "X3_DECIMAL": "0", "X3_TITULO": "Excluído", "X3_DESCRIC": "Linha excluída do dicionário",
         "X3_PROPRI": "U", "X3_CONTEXT": "R", "D_E_L_E_T_": "*"},
    ]
    for ident, (dom, cdom, expdom, expcdom) in enumerate(RELACIONAMENTOS, start=1):
        rows["SX9"].append({
            "X9_DOM": dom, "X9_IDENT": f"{ident:03d}", "X9_CDOM": cdom, "X9_EXPDOM": expdom, "X9_EXPCDOM": expcdom,
        })
    return rows
