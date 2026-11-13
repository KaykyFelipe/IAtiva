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
    agregacao: str = ""  # "" | SUM | COUNT | MIN | MAX | AVG
    tipo: str = "texto"  # texto | data | dinheiro | inteiro | numero | valor | mes | ano
    transformacao: str = ""  # "" | MES | ANO (prefixo da data AAAAMMDD, para agrupar)


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
    top: int = 0  # "top N": limita as linhas (ranking)


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


FILTRO = "filtro-campo"
VALOR_FILTRO_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z -]{0,29}$")


def ler_filtros(texto: str, permitidos) -> list[tuple[str, str]]:
    """"R7_MAT=000123; RA_CODFUNC = 0042" → [(campo, valor)]. Campo fora da lista ou valor fora do
    formato seguro é recusado (ValueError), nunca ignorado em silêncio."""
    out = []
    for parte in (texto or "").split(";"):
        if not parte.strip():
            continue
        campo, sep, valor = parte.partition("=")
        campo, valor = campo.strip().upper(), valor.strip()
        if not sep or campo not in permitidos:
            raise ValueError(f"campo não filtrável: {campo or parte.strip()}")
        if not VALOR_FILTRO_RE.match(valor):
            raise ValueError(f"valor inválido para {campo}")
        out.append((campo, valor))
    return out[:6]


AGREGACAO = "agregacao"
FUNCOES = {"soma": "SUM", "media": "AVG", "contagem": "COUNT", "maximo": "MAX", "minimo": "MIN"}
NOME_FUNCAO = {"SUM": "Soma", "AVG": "Média", "COUNT": "Quantidade", "MAX": "Maior", "MIN": "Menor"}


def _sem_acento(t: str) -> str:
    return unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()


def ler_agregacao(texto: str, spec: dict):
    """"soma:F2_VALBRUT, media:F2_VALBRUT por F2_CLIENTE top 10" -> ([(SUM, campo)...], grupo, top).
    Grupo: campo da consulta, "MES"/"ANO" (do campo de data) ou vazio. Fora disso: ValueError."""
    t = _sem_acento(texto).strip()
    top = 0
    if m := re.search(r"\btop\s*(\d{1,3})\b", t):
        top = max(1, min(100, int(m.group(1))))
        t = (t[:m.start()] + t[m.end():]).strip()
    metricas_txt, _, grupo = t.partition(" por ")
    tipos = {c["campo"]: c["tipo"] for c in spec.get("colunas", [])}
    metricas = []
    for parte in metricas_txt.split(","):
        parte = parte.strip()
        if not parte:
            continue
        func, _, campo = parte.partition(":")
        func, campo = func.strip(), campo.strip().upper()
        if func not in FUNCOES:
            raise ValueError(f"cálculo desconhecido: {func or parte}")
        if FUNCOES[func] == "COUNT" and campo in ("", "*"):
            campo = "*"
        elif campo not in tipos:
            raise ValueError(f"campo fora da consulta: {campo or parte}")
        elif FUNCOES[func] in ("SUM", "AVG") and tipos[campo] != "numero":
            raise ValueError(f"{func} precisa de um campo numérico: {campo}")
        metricas.append((FUNCOES[func], campo))
    if not metricas or len(metricas) > 4:
        raise ValueError("informe de 1 a 4 cálculos (ex.: soma:CAMPO)")
    grupo = grupo.strip().upper()
    if grupo in ('FILIAL', 'FILIAIS'):
        # Cada filial autorizada já é executada separadamente pelo serviço. Não é nome de
        # coluna nem motivo para ampliar o escopo: TODAS/filial continua sendo parâmetro próprio.
        grupo = ''
    if grupo in ("MES", "ANO"):
        if not spec.get("campo_data"):
            raise ValueError("a consulta não tem campo de data para agrupar por mês/ano")
    elif grupo and grupo not in tipos:
        raise ValueError(f"agrupamento por campo fora da consulta: {grupo}")
    return metricas, grupo, top


def ler_top(texto: str) -> int:
    """M41: consulta fixa (ex.: inadimplência) aceita só o ranking: "top 10" -> 10."""
    m = re.search(r"\btop\s*(\d{1,3})\b", str(texto or ""), re.I)
    if not m:
        raise ValueError("Nesta consulta o cálculo aceito é só o ranking dos maiores: \"top N\"")
    return max(1, min(100, int(m.group(1))))


def apelido(spec: dict, campo: str) -> str:
    """Alias SQL do campo na consulta aprendida: e1 (base) ou j1/j2 (tabela juntada)."""
    return next((c.get("alias", "e1") for c in spec.get("colunas", []) if c["campo"] == campo), "e1")


def _aplicar_agregacao(plano, spec: dict, texto: str):
    try:
        metricas, grupo, top = ler_agregacao(texto, spec)
    except ValueError as exc:
        from .compilador import PlanoInvalido

        raise PlanoInvalido(str(exc)) from None
    e1 = "e1"
    rotulos = {c["campo"]: c["rotulo"] for c in spec["colunas"]}
    tipos = {c["campo"]: c["tipo"] for c in spec["colunas"]}
    colunas = []
    if grupo in ("MES", "ANO"):
        colunas.append(Coluna("Mês" if grupo == "MES" else "Ano", Campo(e1, spec["campo_data"]),
                              tipo=grupo.lower(), transformacao=grupo))
    elif grupo:
        colunas.append(Coluna(rotulos[grupo], Campo(apelido(spec, grupo), grupo), tipo=tipos[grupo]))
    filial = next(f.campo for f in plano.filtros if f.campo.nome.endswith("_FILIAL"))
    for func, campo in metricas:
        if campo == "*":
            colunas.append(Coluna("Quantidade", filial, agregacao="COUNT", tipo="inteiro"))
        elif func == "COUNT":
            colunas.append(Coluna(f"Quantidade de {rotulos[campo]}", Campo(apelido(spec, campo), campo), agregacao=func,
                                  tipo="inteiro"))
        else:
            tipo = "valor" if tipos[campo] == "numero" else tipos[campo]
            colunas.append(Coluna(f"{NOME_FUNCAO[func]} de {rotulos[campo]}", Campo(apelido(spec, campo), campo), agregacao=func,
                                  tipo=tipo))
    plano.colunas, plano.agrupar, plano.top = colunas, bool(grupo), top
    primeira_metrica = colunas[1 if grupo else 0].rotulo
    if top:
        plano.ordenar = [(primeira_metrica, True)]
    elif grupo:
        plano.ordenar = [(colunas[0].rotulo, False)]
    else:
        plano.ordenar = []
    plano.premissas.append("Cálculo: " + ", ".join(c.rotulo for c in colunas if c.agregacao)
                           + (f", por {colunas[0].rotulo}" if grupo else "") + (f", top {top}" if top else "") + ".")


REGRAS = "_regras"  # regras da memória (M22): [(campo, operador, valor, origem)] já confirmadas


def medidas_do_plano(plano) -> list[tuple]:
    """O que totalizar sobre todos os registros: (rótulo, função, tipo, campo).
    Consulta que já é cálculo: as próprias medidas. Lista: a soma de cada coluna de valor."""
    if any(c.agregacao for c in plano.colunas):
        return [(c.rotulo, c.agregacao, c.tipo, c.campo) for c in plano.colunas if c.agregacao]
    return [(c.rotulo, "SUM", "dinheiro" if c.tipo == "dinheiro" else "valor", c.campo)
            for c in plano.colunas if c.tipo in ("dinheiro", "numero")]


def plano_total(plano, medidas, campo_filial: str, moeda=None) -> "Plano":
    """Mesmos filtros do plano, sem agrupar por item e sem limite de exibição: uma linha (ou uma por
    moeda) com a quantidade de registros e cada medida. Soma e média levam junto a contagem de
    valores preenchidos, para combinar filiais sem média de médias e distinguir "sem valor" de zero.
    Junções que não entram em filtro nem em medida ficam de fora: não podem inflar o total."""
    usados = {c.alias for _, _, _, c in medidas}
    for f in plano.filtros:
        usados.add(f.campo.alias)
        if isinstance(f.valor, Campo):
            usados.add(f.valor.alias)
    total = Plano(objetivo=plano.objetivo, base=plano.base, filial=plano.filial, filtros=list(plano.filtros),
                  juncoes=[j for j in plano.juncoes if j.alias in usados])
    colunas = [Coluna("moeda", moeda)] if moeda else []
    colunas.append(Coluna("registros", Campo("e1", campo_filial), "COUNT", "inteiro"))
    for i, (_, func, tipo, campo) in enumerate(medidas):
        if func in ("SUM", "AVG"):
            colunas += [Coluna(f"s{i}", campo, "SUM", tipo), Coluna(f"n{i}", campo, "COUNT", "inteiro")]
        else:
            colunas.append(Coluna(f"v{i}", campo, func, tipo))
    total.colunas, total.agrupar = colunas, bool(moeda)
    return total


def plano_chave(juncao, campo_filial: str, filial: str) -> "Plano":
    """Confere a cardinalidade de uma junção: a combinação de campos usada para ligar não pode se
    repetir na outra tabela (dentro da filial). Devolve no máximo uma linha; a última coluna é a
    maior quantidade de repetições."""
    campos = [a for a, _ in juncao.pares]
    p = Plano(objetivo="verificacao", base=juncao.tabela, filial=filial)
    if campo_filial:
        p.filtros.append(Filtro(Campo("e1", campo_filial), "=", filial))
    p.colunas = [Coluna(c, Campo("e1", c)) for c in campos] + [Coluna("repeticoes", Campo("e1", campos[0]), "COUNT", "inteiro")]
    p.agrupar, p.ordenar, p.top = True, [("repeticoes", True)], 1
    return p


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
    for j in spec.get("juncoes", []):
        plano.juncoes.append(Juncao(j["alias"], j["tabela"], tuple((a, b) for a, b in j["pares"])))
        plano.premissas.append(f"Junção com {j['tabela']} pela chave do dicionário (SX9), sem duplicar linhas.")
    plano.filtros.append(Filtro(Campo(e1, filial_campo), "=", valores["filial-analise"]))
    for f in spec.get("filtros_fixos", []):
        # M40: o filtro pode ser de uma tabela juntada (alias da junção) e pode ser "diferente".
        plano.filtros.append(Filtro(Campo(f.get("alias") or e1, f["campo"]), f.get("op") or "=", f["valor"]))
    campo_data = spec.get("campo_data")
    if campo_data:
        periodo = valores["periodo-analise"]
        plano.filtros += [
            Filtro(Campo(e1, campo_data), ">=", _aaaammdd(periodo["inicio"])),
            Filtro(Campo(e1, campo_data), "<=", _aaaammdd(periodo["fim"])),
        ]
        plano.premissas.append(f"Período aplicado ao campo {campo_data}.")
    for f in spec.get("filtros_fixos", []):
        plano.premissas.append(f"Filtro fixo: {f['campo']} {f.get('op') or '='} {f['valor']}.")
    for campo, op, valor, origem in valores.get(REGRAS) or []:
        if op not in ("=", "<>"):
            continue
        plano.filtros.append(Filtro(Campo(e1, campo), op, valor))
        plano.premissas.append(f"Regra da memória ({origem}): {campo} {'igual a' if op == '=' else 'diferente de'} “{valor}”.")
    from iativa.agent.contratos import campo_busca  # noqa: I001

    if campo_busca(spec):
        _aplicar_busca(plano, Campo(apelido(spec, campo_busca(spec)), campo_busca(spec)), valores)
    if valores.get(FILTRO):
        from iativa.agent.contratos import campos_filtraveis

        try:
            filtros = ler_filtros(valores[FILTRO], {c for c, _ in campos_filtraveis(spec)})
        except ValueError as exc:
            from .compilador import PlanoInvalido

            raise PlanoInvalido(str(exc)) from None
        for campo, valor in filtros:
            plano.filtros.append(Filtro(Campo(apelido(spec, campo), campo), "=", valor))
            plano.premissas.append(f"Filtro: {campo} = {valor}.")
    tipos = {"data": "data", "numero": "numero", "texto": "texto"}
    plano.colunas = [Coluna(c["rotulo"], Campo(c.get("alias", "e1"), c["campo"]), tipo=tipos.get(c["tipo"], "texto"))
                     for c in spec["colunas"]]
    rotulos = [c.rotulo for c in plano.colunas]
    ordem = next((c["rotulo"] for c in spec["colunas"] if c["campo"] == campo_data), rotulos[0])
    plano.ordenar = [(ordem, bool(campo_data))]
    if valores.get(AGREGACAO):
        _aplicar_agregacao(plano, spec, valores[AGREGACAO])
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
        if valores.get(AGREGACAO):
            plano.top = ler_top(valores[AGREGACAO])
            plano.premissas.append(f"Ranking: os {plano.top} maiores valores em atraso.")
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
