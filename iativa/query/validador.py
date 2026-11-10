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


def _condicoes(arvore) -> list[exp.EQ]:
    return list(arvore.find_all(exp.EQ))


def _tem_condicao(eqs, alias, sufixo) -> bool:
    for eq in eqs:
        col = eq.left if isinstance(eq.left, exp.Column) else None
        if col is not None and col.table == alias and col.name.endswith(sufixo):
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

    eqs = _condicoes(arvore)
    for t in arvore.find_all(exp.Table):
        if t.args.get("db") or t.args.get("catalog"):
            raise ConsultaRecusada("Nome de tabela com banco ou servidor não é permitido.")
        if t.name not in tabelas_permitidas:
            raise ConsultaRecusada(f"Tabela não permitida: {t.name}.")
        alias = t.alias_or_name
        if not _tem_condicao(eqs, alias, "D_E_L_E_T_"):
            raise ConsultaRecusada(f"Falta o filtro de exclusão lógica em {t.name}.")
        if not _tem_condicao(eqs, alias, "_FILIAL"):
            raise ConsultaRecusada(f"Falta o filtro de filial em {t.name}.")

    topo = arvore.args.get("limit")
    expressao = topo.expression if topo is not None else None
    if not isinstance(expressao, exp.Literal) or not expressao.is_int or int(expressao.this) > limite:
        raise ConsultaRecusada("Falta TOP ou o limite foi excedido.")
    return arvore
