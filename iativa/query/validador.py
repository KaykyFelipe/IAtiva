"""Segunda barreira: validação do SQL pela árvore sintática (sqlglot, dialeto T-SQL).

Mesmo vindo do compilador, o SQL só executa se:
- for exatamente um SELECT, sem INTO, subconsultas, UNION, comandos ou funções de acesso externo;
- usar só tabelas permitidas, sem banco/servidor no nome (bloqueia servidores vinculados);
- cada tabela tiver o filtro de exclusão lógica (D_E_L_E_T_ = ' ') e o de filial;
- tiver TOP dentro do limite.
"""
import sqlglot
from sqlglot import exp

PROIBIDOS = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter, exp.Command, exp.Into,
             exp.Union, exp.Subquery, exp.Merge)
FUNCOES_PROIBIDAS = {"OPENROWSET", "OPENQUERY", "OPENDATASOURCE", "OPENXML", "XP_CMDSHELL"}


class ConsultaRecusada(ValueError):
    pass


def _conjuncoes(cond) -> list:
    """Termos ligados só por AND no nível de cima (sem OR/NOT/parênteses que mudem o sentido)."""
    if cond is None:
        return []
    while isinstance(cond, exp.Paren):
        cond = cond.this
    if isinstance(cond, exp.And):
        return _conjuncoes(cond.left) + _conjuncoes(cond.right)
    return [cond]


def _coluna_e_valor(eq):
    """(coluna, valor) de uma igualdade coluna = literal/parâmetro; None para coluna = coluna e o resto."""
    for col, val in ((eq.left, eq.right), (eq.right, eq.left)):
        if isinstance(col, exp.Column) and isinstance(val, (exp.Literal, exp.Placeholder)):
            return col, val
    return None


def _restringe(conjuncoes, alias, sufixo, exato=None) -> bool:
    """Auditoria (17/11): presença da coluna em qualquer lugar da árvore não prova o filtro. Exige a igualdade
    como termo de AND do nível de cima, na tabela certa, contra literal/parâmetro (e o valor exato, se dado)."""
    for c in conjuncoes:
        if not isinstance(c, exp.EQ):
            continue
        par = _coluna_e_valor(c)
        if par is None:
            continue
        col, val = par
        if col.table != alias or not col.name.upper().endswith(sufixo):
            continue
        if exato is not None and not (isinstance(val, exp.Literal) and val.is_string and val.this == exato):
            continue
        return True
    return False


def validar(sql: str, tabelas_permitidas: set[str], limite: int) -> exp.Select:
    try:
        arvores = sqlglot.parse(sql, read="tsql")
    except sqlglot.errors.ParseError:
        raise ConsultaRecusada("SQL inválido.") from None
    if len(arvores) != 1 or not isinstance(arvores[0], exp.Select):
        raise ConsultaRecusada("Somente um SELECT é permitido.")
    arvore = arvores[0]
    for tipo in PROIBIDOS:
        if arvore.find(tipo) is not None:
            raise ConsultaRecusada(f"Construção proibida: {tipo.__name__}.")
    for f in arvore.find_all(exp.Anonymous, exp.Func):
        nome = (getattr(f, "name", "") or "").upper()
        if nome in FUNCOES_PROIBIDAS:
            raise ConsultaRecusada("Função de acesso externo proibida.")

    onde = _conjuncoes(arvore.args["where"].this) if arvore.args.get("where") else []
    juncoes = {}
    for j in arvore.args.get("joins") or []:
        if isinstance(j.this, exp.Table):
            juncoes[j.this.alias_or_name] = _conjuncoes(j.args.get("on"))
    for t in arvore.find_all(exp.Table):
        if t.args.get("db") or t.args.get("catalog"):
            raise ConsultaRecusada("Nome de tabela com banco ou servidor não é permitido.")
        if t.name not in tabelas_permitidas:
            raise ConsultaRecusada(f"Tabela não permitida: {t.name}.")
        alias = t.alias_or_name
        # Tabela juntada: o filtro vale no ON dela (LEFT JOIN); a base: no WHERE.
        termos = juncoes.get(alias, onde) if alias in juncoes else onde
        if not _restringe(termos, alias, "D_E_L_E_T_", exato=" "):
            raise ConsultaRecusada(f"Falta o filtro de exclusão lógica em {t.name}.")
        if not _restringe(termos, alias, "_FILIAL"):
            raise ConsultaRecusada(f"Falta o filtro de filial em {t.name}.")

    topo = arvore.args.get("limit")
    expressao = topo.expression if topo is not None else None
    if not isinstance(expressao, exp.Literal) or not expressao.is_int or not 0 < int(expressao.this) <= limite:
        raise ConsultaRecusada("Falta TOP ou o limite foi excedido.")
    return arvore
