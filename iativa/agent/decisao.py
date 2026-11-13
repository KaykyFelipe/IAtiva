"""Decisão do agente conversacional (M10): o modelo escreve a resposta em texto corrido e escolhe
UMA ação de uma lista fechada. Quem executa e autoriza a ação é o backend.

Ao modelo vão: a mensagem, o histórico curto da conversa (texto salvo pelo M8, sem resultados),
os objetivos que o usuário pode usar, o estado da solicitação atual, notas da memória do agente e
trechos de conhecimento aprovados. Nunca linhas do ERP (D6).
"""
from dataclasses import dataclass, field

ACOES = (
    "conversar",            # só responder (dúvida, saudação, explicação, pedir informação)
    "preparar_consulta",    # escolher/trocar objetivo e/ou informar parâmetros
    "executar_consulta",    # rodar a consulta que já está completa
    "investigar_divergencia", # somar é etapa: decompor, confrontar fontes e apresentar evidências
    "cancelar_consulta",    # abandonar a solicitação atual
    "aprender_tabela",      # procurar tabelas no dicionário para criar sozinha uma consulta nova (M18)
    "criar_consulta",       # M18: montar a consulta nova a partir de "tabelas_para_aprender"
    "investigar_lista",     # M25: verificar TODOS os integrantes da lista anterior (tarefa em segundo plano)
    "historico_cargo",      # nome → matrículas → histórico de função/cargo → descrições e conclusão
    "listar_aprendizados",  # mostrar consultas aprendidas
    "remover_aprendizado",  # administrador: tirar de uso uma consulta aprendida (pede confirmação)
    "ajustar_filtro",       # M40: a pessoa pediu para mudar os filtros fixos de uma consulta aprendida
    "voltar_filtro",        # M40: desfazer o último ajuste de filtros (volta como era antes)
    "lembrar",              # guardar na memória do agente algo que o usuário ensinou
    "listar_memorias",      # M22: mostrar o que a memória guarda e vale para a pessoa
    "esquecer_memoria",     # M22: desativar uma nota da memória (pede confirmação)
    "listar_pendencias",    # Etapa 6: mostrar propostas aguardando aprovação
    "aprovar_pendencia",    # Etapa 6: aprovar uma proposta (pede confirmação)
    "rejeitar_pendencia",   # Etapa 6: rejeitar uma proposta (pede confirmação)
)
ALCANCES = ("", "usuario", "setor", "empresa")
MAX_RESPOSTA = 2000
MAX_NOTA = 300
MAX_PARAMETROS = 8
MAX_OPCOES = 6
TIPOS_NOTA = ("", "preferencia", "termo", "regra", "correcao")


class DecisaoInvalida(ValueError):
    pass


@dataclass(frozen=True)
class EntradaAgente:
    mensagem: str
    hoje: str
    historico: list[dict]
    objetivos: list[dict]
    solicitacao_atual: dict | None
    notas: list[str] = field(default_factory=list)
    aprendidos: list[dict] = field(default_factory=list)
    trechos: list[dict] = field(default_factory=list)
    administrador: bool = False
    pendencias: list[dict] = field(default_factory=list)
    tabelas_para_aprender: list[dict] = field(default_factory=list)
    # M22: o que já foi tentado NESTA mensagem e como terminou (situação e motivo, sem dados do ERP).
    observacoes: list[dict] = field(default_factory=list)
    # M25: a lista que a pessoa está vendo (só metadados: consulta, tabela e campos do dicionário).
    lista_anterior: dict | None = None


@dataclass(frozen=True)
class Decisao:
    resposta: str
    acao: str
    objetivo: str
    parametros: tuple[tuple[str, str], ...]
    alvo: str
    nota: str
    confirmado: bool
    opcoes: tuple[str, ...]
    alcance: str = ""
    consulta: dict | None = None
    tipo_nota: str = ""
    regra: dict | None = None
    validade: str = ""
    investigacao: dict | None = None
    entrega: str = ""  # M38: o que o pedido exige para estar cumprido (declarado pelo modelo)
    ajuste: dict | None = None  # M40: {"filtros": [{campo, operador, valor}], "remover": [campo]}


ENTREGAS = ("", "resposta", "consulta", "calculo", "verificacao", "investigacao", "historico")


def esquema(codigos_objetivo, codigos_parametro) -> dict:
    return {
        "type": "object",
        "properties": {
            "resposta": {"type": "string", "description": "Texto ao usuário, em português, tom de conversa."},
            "acao": {"type": "string", "enum": list(ACOES)},
            "objetivo": {"type": "string", "enum": [""] + list(codigos_objetivo)},
            "parametros": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "codigo": {"type": "string", "enum": list(codigos_parametro) or [""]},
                        "valor": {"type": "string"},
                    },
                    "required": ["codigo", "valor"],
                },
            },
            "alvo": {"type": "string", "description": "Nome ou código da consulta aprendida, referência da pendência (ex.: nota-3) ou termos da busca."},
            "nota": {"type": "string", "description": "O que guardar na memória (ação lembrar)."},
            "confirmado": {"type": "boolean"},
            "opcoes": {"type": "array", "items": {"type": "string"}},
            "entrega": {"type": "string", "enum": list(ENTREGAS),
                        "description": "O que o pedido exige para estar cumprido: resposta (só explicar), consulta "
                                       "(lista), calculo (soma/contagem/média), verificacao (conferir cada item de uma "
                                       "lista), investigacao (achar a causa de uma diferença), historico (cargos de uma pessoa)."},
            "alcance": {"type": "string", "enum": list(ALCANCES),
                        "description": "Ação lembrar: usuario, setor ou empresa; vazio = padrão."},
            "tipo_nota": {"type": "string", "enum": list(TIPOS_NOTA),
                          "description": "Ação lembrar: preferencia (apresentação), termo (definição), regra (regra de negócio) ou correcao (correção reutilizável)."},
            "regra": {
                "type": "object",
                "description": "Ação lembrar, só quando a pessoa disse o campo e o valor: critério que o sistema aplica nas consultas da tabela.",
                "properties": {"tabela": {"type": "string"}, "campo": {"type": "string"},
                               "operador": {"type": "string", "enum": ["igual", "diferente"]},
                               "valor": {"type": "string"}},
                "required": ["tabela", "campo", "operador", "valor"],
            },
            "investigacao": {
                "type": "object",
                "description": "Ação investigar_lista: campos de lista_anterior que representam o vínculo de cada registro.",
                "properties": {
                    "campos": {"type": "object", "properties": {
                        "inicio": {"type": "string"}, "fim": {"type": "string"}, "situacao": {"type": "string"},
                        "cargo": {"type": "string"}, "categoria": {"type": "string"}},
                        "required": ["inicio", "fim"]},
                    "identidade": {"type": "string", "enum": ["documento", "nome"]},
                },
                "required": ["campos"],
            },
            "validade": {"type": "string", "description": "Ação lembrar: até quando vale (DD/MM/AAAA); vazio = sem prazo."},
            "ajuste": {
                "type": "object",
                "description": "Só na ação ajustar_filtro (consulta em alvo): filtros fixos a incluir/trocar e campos a "
                               "tirar. O campo pode ser da tabela da consulta ou de uma tabela ligada a ela (ex.: F4_DUPLIC "
                               "da TES numa consulta de SD2); a junção é feita pelo dicionário.",
                "properties": {
                    "filtros": {"type": "array", "items": {"type": "object", "properties": {
                        "campo": {"type": "string"}, "operador": {"type": "string", "enum": ["igual", "diferente"]},
                        "valor": {"type": "string"}}, "required": ["campo", "operador", "valor"]}},
                    "remover": {"type": "array", "items": {"type": "string"}},
                },
            },
            "consulta": {
                "type": "object",
                "description": "Só na ação criar_consulta: a consulta nova montada com os campos de tabelas_para_aprender.",
                "properties": {
                    "tabela": {"type": "string"}, "nome": {"type": "string"}, "descricao": {"type": "string"},
                    "colunas": {"type": "array", "items": {"type": "string"}},
                    "campo_data": {"type": "string"}, "campo_busca": {"type": "string"},
                    "filtraveis": {"type": "array", "items": {"type": "string"}},
                    "filtros": {"type": "array", "description": "Filtros fixos que a consulta já nasce aplicando (ex.: os "
                                "mesmos da definição do termo na memória, como F4_DUPLIC = S para faturamento).",
                                "items": {"type": "object", "properties": {
                                    "campo": {"type": "string"},
                                    "operador": {"type": "string", "enum": ["igual", "diferente"]},
                                    "valor": {"type": "string"}}, "required": ["campo", "operador", "valor"]}},
                    "juncoes": {"type": "array", "description": "Outras tabelas de juncoes_possiveis para trazer nomes/descrições.",
                                "items": {"type": "object", "properties": {
                                    "tabela": {"type": "string"},
                                    "colunas": {"type": "array", "items": {"type": "string"}}},
                                    "required": ["tabela", "colunas"]}},
                },
                "required": ["tabela", "nome", "colunas"],
            },
        },
        "required": ["resposta", "acao", "objetivo", "parametros", "alvo", "nota", "confirmado", "opcoes"],
    }


def _texto(v, limite, campo) -> str:
    if not isinstance(v, str) or len(v) > limite:
        raise DecisaoInvalida(campo)
    return v.strip()


def validar(saida, codigos_objetivo, codigos_parametro) -> Decisao:
    if not isinstance(saida, dict):
        raise DecisaoInvalida("formato")
    extras = set(saida) - {"resposta", "acao", "objetivo", "parametros", "alvo", "nota", "confirmado", "opcoes",
                           "alcance", "consulta", "tipo_nota", "regra", "validade", "investigacao", "entrega",
                           "ajuste"}
    if extras:
        raise DecisaoInvalida("campos extras")
    acao = saida.get("acao")
    if acao not in ACOES:
        raise DecisaoInvalida("acao")
    objetivo = saida.get("objetivo") or ""
    if objetivo and objetivo not in codigos_objetivo:
        raise DecisaoInvalida("objetivo")
    params = saida.get("parametros") or []
    if not isinstance(params, list) or len(params) > MAX_PARAMETROS:
        raise DecisaoInvalida("parametros")
    pares = []
    for p in params:
        if not isinstance(p, dict) or p.get("codigo") not in codigos_parametro:
            raise DecisaoInvalida("parametro")
        pares.append((p["codigo"], _texto(p.get("valor", ""), 200, "valor")))
    alcance = saida.get("alcance") or ""
    if alcance not in ALCANCES:
        raise DecisaoInvalida("alcance")
    opcoes = saida.get("opcoes") or []
    if not isinstance(opcoes, list) or len(opcoes) > MAX_OPCOES:
        raise DecisaoInvalida("opcoes")
    consulta = saida.get("consulta") or None
    if consulta is not None:
        consulta = _consulta(consulta)
    if acao == "criar_consulta" and consulta is None:
        raise DecisaoInvalida("consulta")
    tipo_nota = saida.get("tipo_nota") or ""
    if tipo_nota not in TIPOS_NOTA:
        raise DecisaoInvalida("tipo_nota")
    regra = saida.get("regra") or None
    if regra is not None:
        if not isinstance(regra, dict) or set(regra) - {"tabela", "campo", "operador", "valor"}:
            raise DecisaoInvalida("regra")
        regra = {k: _texto(regra.get(k, ""), 40, k) for k in ("tabela", "campo", "operador", "valor")}
    investigacao = saida.get("investigacao") or None
    if investigacao is not None:
        if not isinstance(investigacao, dict) or set(investigacao) - {"campos", "identidade"}:
            raise DecisaoInvalida("investigacao")
        campos = investigacao.get("campos") or {}
        if not isinstance(campos, dict) or set(campos) - {"inicio", "fim", "situacao", "cargo", "categoria"}:
            raise DecisaoInvalida("investigacao.campos")
        investigacao = {"campos": {k: _texto(v, 20, k).upper() for k, v in campos.items() if v},
                        "identidade": "documento" if investigacao.get("identidade") == "documento" else "nome"}
    entrega = saida.get("entrega") or ""
    if entrega not in ENTREGAS:
        raise DecisaoInvalida("entrega")
    ajuste = _ajuste(saida.get("ajuste") or None)
    if acao == "ajustar_filtro" and not ajuste:
        raise DecisaoInvalida("ajuste")
    return Decisao(
        ajuste=ajuste,
        entrega=entrega,
        investigacao=investigacao,
        tipo_nota=tipo_nota, regra=regra, validade=_texto(saida.get("validade", "") or "", 10, "validade"),
        consulta=consulta,
        resposta=_texto(saida.get("resposta", ""), MAX_RESPOSTA, "resposta"),
        acao=acao,
        objetivo=objetivo,
        parametros=tuple(pares),
        alvo=_texto(saida.get("alvo", ""), 200, "alvo"),
        nota=_texto(saida.get("nota", ""), MAX_NOTA, "nota"),
        confirmado=bool(saida.get("confirmado", False)),
        opcoes=tuple(_texto(o, 120, "opcao") for o in opcoes),
        alcance=alcance,
    )


def _ajuste(a) -> dict | None:
    if a is None:
        return None
    if not isinstance(a, dict) or set(a) - {"filtros", "remover"}:
        raise DecisaoInvalida("ajuste")
    filtros, remover = a.get("filtros") or [], a.get("remover") or []
    if not isinstance(filtros, list) or not isinstance(remover, list) or len(filtros) + len(remover) > 6:
        raise DecisaoInvalida("ajuste")
    out = []
    for f in filtros:
        if not isinstance(f, dict) or f.get("operador") not in ("igual", "diferente"):
            raise DecisaoInvalida("ajuste.filtro")
        out.append({"campo": _texto(f.get("campo", ""), 20, "campo").upper(), "operador": f["operador"],
                    "valor": _texto(f.get("valor", ""), 40, "valor")})
    rem = [_texto(c, 20, "remover").upper() for c in remover]
    return {"filtros": out, "remover": rem} if out or rem else None


def _consulta(c) -> dict:
    if not isinstance(c, dict) or set(c) - {"tabela", "nome", "descricao", "colunas", "campo_data", "campo_busca",
                                             "filtraveis", "juncoes", "filtros"}:
        raise DecisaoInvalida("consulta")
    def lista(chave, maximo):
        v = c.get(chave) or []
        if not isinstance(v, list) or len(v) > maximo:
            raise DecisaoInvalida(chave)
        return [_texto(x, 20, chave).upper() for x in v]

    return {
        "tabela": _texto(c.get("tabela", ""), 6, "tabela").upper(),
        "nome": _texto(c.get("nome", ""), 120, "nome"),
        "descricao": _texto(c.get("descricao", "") or "", 300, "descricao"),
        "colunas": lista("colunas", 20),
        "campo_data": _texto(c.get("campo_data", "") or "", 20, "campo_data"),
        "campo_busca": _texto(c.get("campo_busca", "") or "", 20, "campo_busca"),
        "filtraveis": lista("filtraveis", 10),
        "juncoes": _juncoes(c.get("juncoes") or []),
        "filtros": (_ajuste({"filtros": c.get("filtros")}) or {}).get("filtros", []) if c.get("filtros") else [],
    }


def _juncoes(v) -> list[dict]:
    if not isinstance(v, list) or len(v) > 2:
        raise DecisaoInvalida("juncoes")
    out = []
    for j in v:
        if not isinstance(j, dict) or not isinstance(j.get("colunas") or [], list) or len(j.get("colunas") or []) > 5:
            raise DecisaoInvalida("juncao")
        out.append({"tabela": _texto(j.get("tabela", ""), 6, "tabela").upper(),
                    "colunas": [_texto(x, 20, "coluna").upper() for x in j.get("colunas") or []]})
    return out
