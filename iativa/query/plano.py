"""Plano de consulta estruturado (D3), montado pelo backend a partir do objetivo e dos parâmetros.

O modelo de IA não participa: ele só escolheu o objetivo e ajudou a coletar os parâmetros. Aqui
cada objetivo vira um plano com tabelas, junções, filtros, colunas e agrupamento. Nada de texto
livre: o compilador só aceita campos que o catálogo marca como consultáveis.
"""
import re
import unicodedata
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
    op: str  # = <> < <= > >= IN NOT_IN LIKE NOT_LIKE
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


BUSCA = "texto-busca"


def padrao_busca(texto: str) -> str:
    """"Alcans Ltda." → "%ALCANS%LTDA%". Sem acento, maiúsculas (a base usa collation binária e o
    Protheus grava nomes em maiúsculas), só letras/números; curingas do usuário são descartados."""
    t = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode().upper()
    palavras = re.sub(r"[^A-Z0-9]+", " ", t).split()
    miolo = "%".join(palavras)[:38].strip("%")
    return f"%{miolo}%" if miolo else ""


def _aplicar_busca(plano, campo: Campo, valores: dict):
    padrao = padrao_busca(valores.get(BUSCA) or "")
    if padrao:
        plano.filtros.append(Filtro(campo, "LIKE", padrao))
        plano.premissas.append(f"Busca por nome: {campo.nome} contendo “{padrao.strip('%').replace('%', ' ')}”.")


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


def montar_modelo(tabela: str, spec: dict, valores: dict, codigo: str) -> Plano:
    """Plano de uma consulta aprendida (M9): uma tabela, colunas e filtros escolhidos pelo
    administrador, filial obrigatória e período opcional sobre um campo de data."""
    e1 = "e1"
    filial_campo = f"{tabela[1:]}_FILIAL" if tabela.startswith("S") else f"{tabela}_FILIAL"
    plano = Plano(objetivo=codigo, base=tabela, filial=valores["filial-analise"])
    plano.filtros.append(Filtro(Campo(e1, filial_campo), "=", valores["filial-analise"]))
    for f in spec.get("filtros_fixos", []):
        plano.filtros.append(Filtro(Campo(e1, f["campo"]), "=", f["valor"]))
    campo_data = spec.get("campo_data")
    if campo_data:
        periodo = valores["periodo-analise"]
        plano.filtros += [
            Filtro(Campo(e1, campo_data), ">=", _aaaammdd(periodo["inicio"])),
            Filtro(Campo(e1, campo_data), "<=", _aaaammdd(periodo["fim"])),
        ]
        plano.premissas.append(f"Período aplicado ao campo {campo_data}.")
    for f in spec.get("filtros_fixos", []):
        plano.premissas.append(f"Filtro fixo: {f['campo']} = {f['valor']}.")
    from iativa.agent.contratos import campo_busca

    if campo_busca(spec):
        _aplicar_busca(plano, Campo(e1, campo_busca(spec)), valores)
    tipos = {"data": "data", "numero": "numero", "texto": "texto"}
    plano.colunas = [Coluna(c["rotulo"], Campo(e1, c["campo"]), tipo=tipos.get(c["tipo"], "texto"))
                     for c in spec["colunas"]]
    rotulos = [c.rotulo for c in plano.colunas]
    ordem = next((c["rotulo"] for c in spec["colunas"] if c["campo"] == campo_data), rotulos[0])
    plano.ordenar = [(ordem, bool(campo_data))]
    plano.premissas += ["Consulta aprendida e aprovada (M9).", "Registros excluídos (D_E_L_E_T_) ignorados."]
    return plano


def montar(objetivo: str, valores: dict) -> Plano:
    """`valores`: código do parâmetro → valor já validado (resolução da memória)."""
    from iativa.agent.contratos import PREFIXO_APRENDIDO

    if objetivo.startswith(PREFIXO_APRENDIDO):
        from iativa.agent.models import ConsultaModelo, SituacaoModelo

        cm = ConsultaModelo.objects.get(codigo=objetivo, situacao=SituacaoModelo.APROVADA)
        return montar_modelo(cm.tabela, cm.spec, valores, cm.codigo)
    plano = Plano(objetivo=objetivo)
    e1, a1 = "e1", "a1"
    if objetivo == "titulos-em-atraso":
        corte = date.fromisoformat(valores["data-corte"])
        limite = corte - timedelta(days=int(valores["dias-tolerancia-atraso"]))
        _comuns(plano, valores["filial-analise"])
        _aplicar_busca(plano, Campo(a1, "A1_NOME"), valores)
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
        _aplicar_busca(plano, Campo(a1, "A1_NOME"), valores)
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
