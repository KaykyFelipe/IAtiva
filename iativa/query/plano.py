"""Plano de consulta estruturado (D3), montado pelo backend a partir do objetivo e dos parâmetros.

O modelo de IA não participa: ele só escolheu o objetivo e ajudou a coletar os parâmetros. Aqui
cada objetivo vira um plano com tabelas, junções, filtros, colunas e agrupamento. Nada de texto
livre: o compilador só aceita campos que o catálogo marca como consultáveis.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta

# Tipos de título fora da análise de recebíveis. PREMISSA a confirmar com o Financeiro (info 8).
TIPOS_EXCLUIDOS = ("RA", "NCC", "PR")
PREMISSA_TIPOS = "Tipos excluídos: RA (adiantamento), NCC (crédito), PR (provisório) e abatimentos (tipo terminado em “-”)."
PREMISSA_SALDO = "Saldo atual do título: pagamentos feitos depois da data de corte já estão descontados."


@dataclass(frozen=True)
class Campo:
    alias: str  # e1 | a1
    nome: str


@dataclass(frozen=True)
class Filtro:
    campo: Campo
    op: str  # = <> < <= > >= IN NOT_IN NOT_LIKE
    valor: object  # valor literal (vira parâmetro) ou Campo


@dataclass(frozen=True)
class Coluna:
    rotulo: str
    campo: Campo
    agregacao: str = ""  # "" | SUM | COUNT | MIN | MAX
    tipo: str = "texto"  # texto | data | dinheiro | inteiro


@dataclass(frozen=True)
class Juncao:
    alias: str
    tabela: str
    pares: tuple[tuple[str, str], ...]  # (campo da junção, campo de e1)


@dataclass
class Plano:
    objetivo: str
    base: str = "SE1"
    juncoes: list[Juncao] = field(default_factory=list)
    filtros: list[Filtro] = field(default_factory=list)
    colunas: list[Coluna] = field(default_factory=list)
    agrupar: bool = False
    ordenar: list[tuple[str, bool]] = field(default_factory=list)  # (rótulo, decrescente)
    premissas: list[str] = field(default_factory=list)
    chave_unica: tuple[str, ...] = ()  # rótulos que não podem repetir (detecção de duplicação)
    filial: str = ""


def _aaaammdd(iso: str) -> str:
    return iso.replace("-", "")


def _tratamento(valores, plano) -> Campo:
    """Coluna de valor conforme o tratamento de baixa parcial."""
    e1 = "e1"
    tratamento = valores["tratamento-baixa-parcial"]
    if tratamento == "considerar-saldo":
        plano.premissas.append("Valor considerado: saldo em aberto (baixas parciais descontadas).")
        return Campo(e1, "E1_SALDO")
    if tratamento == "considerar-valor-original":
        plano.premissas.append("Valor considerado: valor original do título, mesmo com baixa parcial.")
        return Campo(e1, "E1_VALOR")
    plano.premissas.append("Títulos com baixa parcial foram desconsiderados.")
    plano.filtros.append(Filtro(Campo(e1, "E1_SALDO"), "=", Campo(e1, "E1_VALOR")))
    return Campo(e1, "E1_VALOR")


def _comuns(plano, filial):
    e1 = "e1"
    plano.filial = filial
    plano.filtros += [
        Filtro(Campo(e1, "E1_FILIAL"), "=", filial),
        Filtro(Campo(e1, "E1_SALDO"), ">", 0),
        Filtro(Campo(e1, "E1_TIPO"), "NOT_IN", TIPOS_EXCLUIDOS),
        Filtro(Campo(e1, "E1_TIPO"), "NOT_LIKE", "%-"),
    ]
    plano.juncoes.append(Juncao("a1", "SA1", (("A1_COD", "E1_CLIENTE"), ("A1_LOJA", "E1_LOJA"))))
    plano.premissas += [PREMISSA_TIPOS, PREMISSA_SALDO, "Registros excluídos (D_E_L_E_T_) ignorados."]


def montar(objetivo: str, valores: dict) -> Plano:
    """`valores`: código do parâmetro → valor já validado (resolução da memória)."""
    plano = Plano(objetivo=objetivo)
    e1, a1 = "e1", "a1"
    if objetivo == "titulos-em-atraso":
        corte = date.fromisoformat(valores["data-corte"])
        limite = corte - timedelta(days=int(valores["dias-tolerancia-atraso"]))
        _comuns(plano, valores["filial-analise"])
        valor = _tratamento(valores, plano)
        plano.filtros += [
            Filtro(Campo(e1, "E1_EMISSAO"), "<=", corte.strftime("%Y%m%d")),
            Filtro(Campo(e1, "E1_VENCREA"), ">", "19000101"),
            Filtro(Campo(e1, "E1_VENCREA"), "<", limite.strftime("%Y%m%d")),
        ]
        plano.colunas = [
            Coluna("Cliente", Campo(e1, "E1_CLIENTE")),
            Coluna("Loja", Campo(e1, "E1_LOJA")),
            Coluna("Nome", Campo(a1, "A1_NOME")),
            Coluna("Títulos", Campo(e1, "E1_NUM"), "COUNT", "inteiro"),
            Coluna("Valor em atraso", valor, "SUM", "dinheiro"),
            Coluna("Vencimento mais antigo", Campo(e1, "E1_VENCREA"), "MIN", "data"),
        ]
        plano.agrupar = True
        plano.ordenar = [("Valor em atraso", True)]
        plano.chave_unica = ("Cliente", "Loja")
        plano.premissas.append(
            f"Em atraso: vencimento real antes de {limite:%d/%m/%Y} (corte {corte:%d/%m/%Y} menos a tolerância)."
        )
        return plano

    if objetivo == "titulos-a-vencer":
        periodo = valores["periodo-analise"]
        _comuns(plano, valores["filial-analise"])
        valor = _tratamento(valores, plano)
        plano.filtros += [
            Filtro(Campo(e1, "E1_VENCREA"), ">=", _aaaammdd(periodo["inicio"])),
            Filtro(Campo(e1, "E1_VENCREA"), "<=", _aaaammdd(periodo["fim"])),
        ]
        plano.colunas = _colunas_titulo(valor)
        plano.ordenar = [("Vencimento real", False), ("Cliente", False)]
        plano.chave_unica = ("Prefixo", "Número", "Parcela", "Tipo")
        return plano

    if objetivo == "posicao-cliente":
        cod, _, loja = valores["cliente-especifico"].partition("-")
        corte = date.fromisoformat(valores["data-corte"])
        _comuns(plano, valores["filial-analise"])
        valor = _tratamento(valores, plano)
        plano.filtros += [
            Filtro(Campo(e1, "E1_CLIENTE"), "=", cod.strip()),
            Filtro(Campo(e1, "E1_LOJA"), "=", (loja or "01").strip()),
            Filtro(Campo(e1, "E1_EMISSAO"), "<=", corte.strftime("%Y%m%d")),
        ]
        plano.colunas = _colunas_titulo(valor)
        plano.ordenar = [("Vencimento real", False)]
        plano.chave_unica = ("Prefixo", "Número", "Parcela", "Tipo")
        plano.premissas.append(f"Títulos vencidos: vencimento real antes de {corte:%d/%m/%Y}.")
        return plano

    raise ValueError("Objetivo sem plano de consulta.")


def _colunas_titulo(valor: Campo) -> list[Coluna]:
    e1, a1 = "e1", "a1"
    return [
        Coluna("Cliente", Campo(e1, "E1_CLIENTE")),
        Coluna("Loja", Campo(e1, "E1_LOJA")),
        Coluna("Nome", Campo(a1, "A1_NOME")),
        Coluna("Prefixo", Campo(e1, "E1_PREFIXO")),
        Coluna("Número", Campo(e1, "E1_NUM")),
        Coluna("Parcela", Campo(e1, "E1_PARCELA")),
        Coluna("Tipo", Campo(e1, "E1_TIPO")),
        Coluna("Emissão", Campo(e1, "E1_EMISSAO"), tipo="data"),
        Coluna("Vencimento real", Campo(e1, "E1_VENCREA"), tipo="data"),
        Coluna("Valor", valor, tipo="dinheiro"),
    ]
