"""Compila o plano em T-SQL parametrizado, usando só nomes do catálogo.

- Tabelas: nome físico vindo do dicionário (SX2), nunca do plano.
- Campos: precisam existir no catálogo como consultáveis (ou ser técnicos de controle).
- Valores: sempre parâmetros `?`. Textos são completados com espaços até o tamanho do campo
  (o Protheus grava CHAR com espaços; no SQL Server a comparação já ignora, no SQLite não).
- Filial: conteúdo calculado pelo nível de compartilhamento de cada tabela (xFilial).
"""
from dataclasses import dataclass

from .plano import Campo, Plano

TECNICOS = {"D_E_L_E_T_"}
OPERADORES = {"=": "=", "<>": "<>", "<": "<", "<=": "<=", ">": ">", ">=": ">="}
TAMANHO_EMPRESA = 3  # filial = 3 posições da empresa + 3 da filial (documento aprovado)


class PlanoInvalido(ValueError):
    pass


@dataclass(frozen=True)
class Compilado:
    sql: str
    params: tuple
    rotulos: tuple[str, ...]
    tipos: tuple[str, ...]


def xfilial(tabela, filial: str) -> str:
    campo = next((c for c in tabela.campos if c.nome == tabela.campo_filial), None)
    tamanho = (campo.tamanho if campo and campo.tamanho else len(filial)) or len(filial)
    nivel = tabela.compartilhamento
    if nivel == "filial":
        return filial.ljust(tamanho)
    if nivel == "compartilhado":
        return " " * tamanho
    if nivel == "empresa":
        return filial[:TAMANHO_EMPRESA].ljust(tamanho)
    raise PlanoInvalido(f"Compartilhamento '{nivel}' de {tabela.alias} não suportado.")


class _Montador:
    def __init__(self, plano: Plano, catalogo, limite: int):
        aliases = [plano.base] + [j.tabela for j in plano.juncoes]
        self.tabelas = catalogo.tabelas(aliases, com_campos=True)
        faltando = set(aliases) - set(self.tabelas)
        if faltando:
            raise PlanoInvalido(f"Tabelas indisponíveis no catálogo: {sorted(faltando)}")
        self.por_alias = {"e1": self.tabelas[plano.base]} | {j.alias: self.tabelas[j.tabela] for j in plano.juncoes}
        self.plano, self.limite = plano, limite

    def _info(self, campo: Campo):
        tabela = self.por_alias.get(campo.alias)
        if tabela is None:
            raise PlanoInvalido("Alias desconhecido.")
        if campo.nome in TECNICOS:
            return tabela, None
        info = next((c for c in tabela.campos if c.nome == campo.nome), None)
        if info is None or not info.consultavel:
            raise PlanoInvalido(f"Campo {campo.nome} não consultável.")
        return tabela, info

    def ref(self, campo: Campo) -> str:
        self._info(campo)
        return f"{campo.alias}.{campo.nome}"

    def valor(self, campo: Campo, v):
        _, info = self._info(campo)
        if isinstance(v, str) and info is not None and info.tipo in ("C", "D") and info.tamanho:
            return v.ljust(info.tamanho)
        return v

    def condicao(self, f, params: list) -> str:
        esquerda = self.ref(f.campo)
        if isinstance(f.valor, Campo):
            if f.op not in OPERADORES:
                raise PlanoInvalido("Operador inválido entre campos.")
            return f"{esquerda} {OPERADORES[f.op]} {self.ref(f.valor)}"
        if f.op in OPERADORES:
            params.append(self.valor(f.campo, f.valor))
            return f"{esquerda} {OPERADORES[f.op]} ?"
        if f.op == "NOT_IN":
            itens = [self.valor(f.campo, v) for v in f.valor]
            params.extend(itens)
            return f"{esquerda} NOT IN ({', '.join('?' for _ in itens)})"
        if f.op == "LIKE":  # busca por nome: padrão montado por `plano.padrao_busca`
            params.append(f.valor)
            return f"{esquerda} LIKE ?"
        if f.op == "NOT_LIKE":
            params.append(f.valor)
            return f"{esquerda} NOT LIKE ?"
        raise PlanoInvalido("Operador desconhecido.")

    def montar(self) -> Compilado:
        p = self.plano
        params: list = []
        selecao, agrupar = [], []
        for i, c in enumerate(p.colunas):
            r = self.ref(c.campo)
            if c.transformacao:
                if c.transformacao not in ("MES", "ANO"):
                    raise PlanoInvalido("Transformação inválida.")
                r = f"SUBSTRING({r}, 1, {6 if c.transformacao == 'MES' else 4})"
            if c.agregacao:
                if c.agregacao not in ("SUM", "COUNT", "MIN", "MAX", "AVG"):
                    raise PlanoInvalido("Agregação inválida.")
                selecao.append(f"{c.agregacao}({r}) AS c{i}")
            else:
                selecao.append(f"{r} AS c{i}")
                agrupar.append(r)
        base = self.por_alias["e1"]
        juncoes = []
        for j in p.juncoes:
            t = self.por_alias[j.alias]
            conds = []
            if t.campo_filial:
                params.append(xfilial(t, p.filial))
                conds.append(f"{j.alias}.{t.campo_filial} = ?")
            conds += [f"{self.ref(Campo(j.alias, a))} = {self.ref(Campo('e1', b))}" for a, b in j.pares]
            if t.exclusao_logica:
                conds.append(f"{j.alias}.D_E_L_E_T_ = ' '")
            juncoes.append(f"LEFT JOIN {t.fisica} AS {j.alias} ON " + " AND ".join(conds))
        where = []
        if base.exclusao_logica:
            where.append("e1.D_E_L_E_T_ = ' '")
        for f in p.filtros:
            if f.campo.nome == base.campo_filial and f.op == "=":
                params.append(xfilial(base, f.valor))
                where.append(f"e1.{base.campo_filial} = ?")
                continue
            where.append(self.condicao(f, params))
        rotulos = [c.rotulo for c in p.colunas]
        ordem = [f"c{rotulos.index(r)} {'DESC' if desc else 'ASC'}" for r, desc in p.ordenar]
        sql = (
            f"SELECT TOP {min(self.limite + 1, p.top) if p.top else self.limite + 1} {', '.join(selecao)} FROM {base.fisica} AS e1 "
            + " ".join(juncoes)
            + (" WHERE " + " AND ".join(where) if where else "")
            + (" GROUP BY " + ", ".join(agrupar) if p.agrupar and agrupar else "")
            + (" ORDER BY " + ", ".join(ordem) if ordem else "")
        )
        # Junções vêm antes do WHERE no texto: os parâmetros já foram acumulados nessa ordem.
        return Compilado(sql, tuple(params), tuple(rotulos), tuple(c.tipo for c in p.colunas))


def compilar(plano: Plano, catalogo, limite: int) -> Compilado:
    return _Montador(plano, catalogo, limite).montar()
