"""Entrada e saída estruturadas do modelo, com validação estrita no backend.

A saída do modelo nunca é confiável por si: objetivo e códigos de parâmetro precisam estar no
contrato, textos têm tamanho limitado e campos extras são recusados. Saída inválida = falha do
modelo (o backend não tenta "consertar").
"""
from dataclasses import dataclass, field

INTENCOES = ("nova_consulta", "continuar", "corrigir", "cancelar", "fora_do_escopo", "ajuda")
MAX_PARAMETROS = 8
MAX_VALOR = 200
MAX_PERGUNTA = 300
MAX_OPCOES = 5
MAX_OBSERVACAO = 400


class SaidaInvalida(ValueError):
    pass


@dataclass(frozen=True)
class EntradaModelo:
    """Tudo o que vai ao modelo. Nunca inclui linhas de resultado (D6)."""

    mensagem: str
    hoje: str
    objetivos: list[dict]
    solicitacao_atual: dict | None
    trechos: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class Interpretacao:
    intencao: str
    objetivo: str
    parametros: tuple[tuple[str, str], ...]
    pergunta: str
    opcoes: tuple[str, ...]
    observacao: str


def esquema_json(codigos_objetivo: list[str], codigos_parametro: list[str]) -> dict:
    """JSON Schema para structured outputs (sem restrições de tamanho: checadas em `validar`)."""
    return {
        "type": "object",
        "properties": {
            "intencao": {"type": "string", "enum": list(INTENCOES)},
            "objetivo": {"type": "string", "enum": [""] + list(codigos_objetivo)},
            "parametros": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "codigo": {"type": "string", "enum": list(codigos_parametro)},
                        "valor": {"type": "string"},
                    },
                    "required": ["codigo", "valor"],
                    "additionalProperties": False,
                },
            },
            "ambiguidade": {
                "type": "object",
                "properties": {
                    "pergunta": {"type": "string"},
                    "opcoes": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["pergunta", "opcoes"],
                "additionalProperties": False,
            },
            "observacao": {"type": "string"},
        },
        "required": ["intencao", "objetivo", "parametros", "ambiguidade", "observacao"],
        "additionalProperties": False,
    }


def _texto(valor, limite, campo) -> str:
    if not isinstance(valor, str) or len(valor) > limite:
        raise SaidaInvalida(f"{campo} inválido")
    return valor.strip()


def validar(saida, codigos_objetivo, codigos_parametro) -> Interpretacao:
    if not isinstance(saida, dict) or set(saida) != {"intencao", "objetivo", "parametros", "ambiguidade", "observacao"}:
        raise SaidaInvalida("campos da saída")
    intencao = saida["intencao"]
    if intencao not in INTENCOES:
        raise SaidaInvalida("intencao")
    objetivo = saida["objetivo"]
    if objetivo != "" and objetivo not in codigos_objetivo:
        raise SaidaInvalida("objetivo")
    params = saida["parametros"]
    if not isinstance(params, list) or len(params) > MAX_PARAMETROS:
        raise SaidaInvalida("parametros")
    pares = []
    for p in params:
        if not isinstance(p, dict) or set(p) != {"codigo", "valor"} or p["codigo"] not in codigos_parametro:
            raise SaidaInvalida("parametro")
        pares.append((p["codigo"], _texto(p["valor"], MAX_VALOR, "valor")))
    amb = saida["ambiguidade"]
    if not isinstance(amb, dict) or set(amb) != {"pergunta", "opcoes"}:
        raise SaidaInvalida("ambiguidade")
    opcoes = amb["opcoes"]
    if not isinstance(opcoes, list) or len(opcoes) > MAX_OPCOES:
        raise SaidaInvalida("opcoes")
    return Interpretacao(
        intencao=intencao,
        objetivo=objetivo,
        parametros=tuple(pares),
        pergunta=_texto(amb["pergunta"], MAX_PERGUNTA, "pergunta"),
        opcoes=tuple(_texto(o, MAX_VALOR, "opcao") for o in opcoes),
        observacao=_texto(saida["observacao"], MAX_OBSERVACAO, "observacao"),
    )
