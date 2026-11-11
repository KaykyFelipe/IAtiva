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
    "cancelar_consulta",    # abandonar a solicitação atual
    "aprender_tabela",      # procurar tabelas no dicionário para criar sozinha uma consulta nova (M18)
    "criar_consulta",       # M18: montar a consulta nova a partir de "tabelas_para_aprender"
    "listar_aprendizados",  # mostrar consultas aprendidas
    "remover_aprendizado",  # administrador: tirar de uso uma consulta aprendida (pede confirmação)
    "lembrar",              # guardar na memória do agente algo que o usuário ensinou
    "listar_pendencias",    # Etapa 6: mostrar propostas aguardando aprovação
    "aprovar_pendencia",    # Etapa 6: aprovar uma proposta (pede confirmação)
    "rejeitar_pendencia",   # Etapa 6: rejeitar uma proposta (pede confirmação)
)
ALCANCES = ("", "usuario", "setor", "empresa")
MAX_RESPOSTA = 2000
MAX_NOTA = 300
MAX_PARAMETROS = 8
MAX_OPCOES = 6


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
            "alcance": {"type": "string", "enum": list(ALCANCES),
                        "description": "Ação lembrar: usuario, setor ou empresa; vazio = padrão."},
            "consulta": {
                "type": "object",
                "description": "Só na ação criar_consulta: a consulta nova montada com os campos de tabelas_para_aprender.",
                "properties": {
                    "tabela": {"type": "string"}, "nome": {"type": "string"}, "descricao": {"type": "string"},
                    "colunas": {"type": "array", "items": {"type": "string"}},
                    "campo_data": {"type": "string"}, "campo_busca": {"type": "string"},
                    "filtraveis": {"type": "array", "items": {"type": "string"}},
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
                           "alcance", "consulta"}
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
    return Decisao(
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


def _consulta(c) -> dict:
    if not isinstance(c, dict) or set(c) - {"tabela", "nome", "descricao", "colunas", "campo_data", "campo_busca",
                                             "filtraveis"}:
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
    }
