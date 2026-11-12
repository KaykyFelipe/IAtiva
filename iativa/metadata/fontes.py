"""Adaptadores de metadados.

Todas as consultas aqui são FIXAS e parametrizadas (nenhum SQL é montado a partir de texto do
usuário ou do modelo) e leem somente estrutura: nomes de tabelas, colunas, títulos e chaves.

- `FisicoSqlite`: estrutura física da base sintética (PRAGMA).
- `InformationSchemaSqlServer`: estrutura física do SQL Server via INFORMATION_SCHEMA.
- `DicionarioProtheus`: dicionário no formato SX2/SX3/SIX/SX9, quando ele estiver no banco.
  A forma de acesso ao dicionário real ainda não foi confirmada (informação 4); o adaptador é
  exercitado só com o dicionário sintético.

As conexões são DB-API (sqlite3 ou pyodbc, ambos com marcador `?`).
"""
import re

from .tipos import CampoDicionario, ColunaFisica, Relacionamento, TabelaDicionario

ALIAS_RE = re.compile(r"^[A-Z][A-Z0-9]{2}$")
FISICA_RE = re.compile(r"^[A-Z][A-Z0-9]{2}[0-9]{3}$")
CAMPO_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,9}$")


class FonteInvalida(ValueError):
    pass


def validar_alias(alias: str) -> str:
    if not isinstance(alias, str) or not ALIAS_RE.match(alias):
        raise FonteInvalida("Alias de tabela inválido.")
    return alias


def validar_fisica(nome: str) -> str:
    if not isinstance(nome, str) or not FISICA_RE.match(nome):
        raise FonteInvalida("Nome físico de tabela inválido.")
    return nome


CAMPO_EM_EXPR_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,2}_[A-Z0-9_]+\b")


def _expressao(expr: str) -> tuple[str, ...]:
    """Campos de uma expressão de chave do Protheus (ex.: `E5_FILIAL+DTOS(E5_DATA)+STR(E5_VALOR,14,2)`).

    Cada parte precisa conter exatamente um campo; funções de conversão (DTOS, STR...) são descartadas.
    """
    campos = []
    for parte in (expr or "").upper().split("+"):
        if not parte.strip():
            continue
        achados = CAMPO_EM_EXPR_RE.findall(parte)
        if len(achados) != 1 or not CAMPO_RE.match(achados[0]):
            raise FonteInvalida("Expressão de chave fora do formato esperado.")
        campos.append(achados[0])
    if not campos:
        raise FonteInvalida("Expressão de chave vazia.")
    return tuple(campos)


def _marcadores(n: int) -> str:
    return ", ".join("?" for _ in range(n))


class FisicoSqlite:
    """Colunas físicas da base sintética (SQLite)."""

    def __init__(self, conn):
        self.conn = conn

    def colunas(self, fisica: str) -> dict[str, ColunaFisica]:
        validar_fisica(fisica)
        # PRAGMA não aceita parâmetro; o nome já foi validado por expressão regular.
        rows = self.conn.execute(f'PRAGMA table_info("{fisica}")').fetchall()
        return {r[1]: ColunaFisica(r[1], r[2]) for r in rows}


class InformationSchemaSqlServer:
    """Colunas físicas no SQL Server. Com collation binária, nomes são comparados exatamente."""

    SQL = (
        "SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, NUMERIC_PRECISION, NUMERIC_SCALE "
        "FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? ORDER BY ORDINAL_POSITION"
    )

    def __init__(self, conn, esquema: str = "dbo"):
        self.conn = conn
        self.esquema = esquema

    def colunas(self, fisica: str) -> dict[str, ColunaFisica]:
        validar_fisica(fisica)
        cursor = self.conn.cursor()
        try:
            cursor.execute(self.SQL, (self.esquema, fisica))
            out = {}
            for nome, tipo, tam, prec, escala in cursor.fetchall():
                nome = nome.strip()
                if tam is not None:
                    tipo = f"{tipo}({tam})"
                elif prec is not None:
                    tipo = f"{tipo}({prec},{escala or 0})"
                out[nome] = ColunaFisica(nome, tipo)
            return out
        finally:
            cursor.close()


class DicionarioProtheus:
    """Leitura do dicionário SX2/SX3/SIX/SX9 armazenado no banco (registros não excluídos)."""

    def __init__(self, conn, sufixo: str = "010"):
        if not re.fullmatch(r"[0-9]{3}", sufixo or ""):
            raise FonteInvalida("Sufixo de empresa inválido.")
        self.conn = conn
        self.sx2, self.sx3, self.six, self.sx9 = (f"{t}{sufixo}" for t in ("SX2", "SX3", "SIX", "SX9"))

    def _consultar(self, sql: str, params: tuple) -> list[tuple]:
        cursor = self.conn.cursor()
        try:
            cursor.execute(sql, params)
            return [tuple(v.strip() if isinstance(v, str) else v for v in r) for r in cursor.fetchall()]
        finally:
            cursor.close()

    def tabelas(self, aliases: list[str]) -> dict[str, TabelaDicionario]:
        aliases = [validar_alias(a) for a in aliases]
        if not aliases:
            return {}
        rows = self._consultar(
            f"SELECT X2_CHAVE, X2_ARQUIVO, X2_NOME, X2_MODO, X2_MODOUN, X2_MODOEMP FROM {self.sx2} "
            f"WHERE D_E_L_E_T_ = ' ' AND X2_CHAVE IN ({_marcadores(len(aliases))})",
            tuple(aliases),
        )
        return {r[0]: TabelaDicionario(*r) for r in rows}

    def campos(self, alias: str) -> list[CampoDicionario]:
        validar_alias(alias)
        rows = self._consultar(
            "SELECT X3_CAMPO, X3_TIPO, X3_TAMANHO, X3_DECIMAL, X3_TITULO, X3_DESCRIC, X3_PROPRI, X3_CONTEXT "
            f"FROM {self.sx3} WHERE D_E_L_E_T_ = ' ' AND X3_ARQUIVO = ? ORDER BY X3_ORDEM",
            (alias,),
        )
        return [
            CampoDicionario(
                tabela=alias, nome=r[0], tipo=r[1], tamanho=int(r[2] or 0), decimal=int(r[3] or 0),
                titulo=r[4], descricao=r[5], personalizado=r[6] == "U", virtual=r[7] == "V",
            )
            for r in rows
        ]

    def indice(self) -> tuple[list[tuple], list[tuple]]:
        """Nomes de todas as tabelas (SX2) e títulos/descrições dos campos reais (SX3), para a
        busca do aprendizado (M9). Só estrutura; nenhum dado. A comparação (sem acento, por
        radical) é feita em Python porque o LIKE com collation binária não ignora acentos."""
        tabelas = self._consultar(f"SELECT X2_CHAVE, X2_NOME FROM {self.sx2} WHERE D_E_L_E_T_ = ' '", ())
        campos = self._consultar(
            f"SELECT X3_ARQUIVO, X3_CAMPO, X3_TITULO, X3_DESCRIC FROM {self.sx3} "
            "WHERE D_E_L_E_T_ = ' ' AND X3_CONTEXT <> 'V'", ()
        )
        return tabelas, campos

    def chave_primaria(self, alias: str) -> tuple[str, ...]:
        validar_alias(alias)
        rows = self._consultar(
            f"SELECT CHAVE FROM {self.six} WHERE D_E_L_E_T_ = ' ' AND INDICE = ? AND ORDEM = '1'", (alias,)
        )
        if not rows:
            return ()
        try:
            return _expressao(rows[0][0])
        except FonteInvalida:
            return ()

    def relacionados(self, alias: str) -> list[Relacionamento]:
        """Relacionamentos em que `alias` aponta para outra tabela (X9_CDOM = alias)."""
        validar_alias(alias)
        rows = self._consultar(
            f"SELECT X9_DOM, X9_CDOM, X9_EXPDOM, X9_EXPCDOM FROM {self.sx9} "
            "WHERE D_E_L_E_T_ = ' ' AND X9_CDOM = ? ORDER BY X9_DOM, X9_IDENT", (alias,),
        )
        out = []
        for dom, cdom, expdom, expcdom in rows:
            try:
                origem, destino = _expressao(expdom), _expressao(expcdom)
            except FonteInvalida:
                continue
            if len(origem) == len(destino) and dom.strip() != alias:
                out.append(Relacionamento(dom.strip(), cdom.strip(), origem, destino))
        return out

    def relacionamentos(self, aliases: list[str]) -> list[Relacionamento]:
        aliases = [validar_alias(a) for a in aliases]
        if len(aliases) < 2:
            return []
        m = _marcadores(len(aliases))
        rows = self._consultar(
            f"SELECT X9_DOM, X9_CDOM, X9_EXPDOM, X9_EXPCDOM FROM {self.sx9} "
            f"WHERE D_E_L_E_T_ = ' ' AND X9_DOM IN ({m}) AND X9_CDOM IN ({m}) ORDER BY X9_DOM, X9_IDENT",
            tuple(aliases) * 2,
        )
        out = []
        for dom, cdom, expdom, expcdom in rows:
            try:
                origem, destino = _expressao(expdom), _expressao(expcdom)
            except FonteInvalida:
                continue  # expressão que não sabemos ler com segurança não é oferecida
            if len(origem) != len(destino):
                continue  # relacionamento incompleto não é oferecido
            out.append(Relacionamento(dom, cdom, origem, destino))
        return out
