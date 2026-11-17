"""Etapa 2 (M74): definição de negócio que falta vira pergunta objetiva, antes de consultar.

Geral, sem frase fixa no código: o glossário aprovado (`knowledge/glossario/*.md`) marca o termo que ainda não
tem definição ("Ainda sem definição aprovada. Perguntar ao usuário: ..."). Se o pedido usa esse termo e não há
definição na memória (nota ativa da pessoa ou do setor), nem no próprio pedido, a IA pergunta o que falta em
vez de supor um critério. A resposta da pessoa segue no histórico e não é perguntada de novo.
"""
import re
from dataclasses import dataclass

from .periodo import _norm

_MARCA = re.compile(r"Ainda sem definição aprovada\.\s*Perguntar ao usuário:\s*(.+)", re.S | re.I)
# O próprio pedido já traz o critério ("considere inadimplente quem tem título vencido há mais de 30 dias").
_DEFINIDO = re.compile(r"\b(consider\w*|defin\w*|criterio|significa|entend\w* como|ou seja|"
                       r"mais de \d+ dias|ha \d+ dias|\d+ dias de atraso|tolerancia)\b")


@dataclass(frozen=True)
class Indefinido:
    termo: str
    perguntar: str
    radical: str


def _indefinidos() -> list[Indefinido]:
    try:
        from iativa.knowledge.documentos import carregar_todos
        docs = carregar_todos()
    except Exception:
        return []
    out = []
    for doc in docs:
        if not doc.recuperavel:
            continue
        for s in doc.secoes:
            m = _MARCA.search(s.texto)
            if m:
                termo = s.titulo.strip()
                out.append(Indefinido(termo, " ".join(m.group(1).split()).rstrip("."), _norm(termo)[:6]))
    return out


def _na_memoria(user, radical) -> bool:
    from .memoria import notas_relevantes
    try:
        return any(radical in _norm(n.texto) for n in notas_relevantes(user, radical))
    except Exception:
        return False


def _consulta_define(radical) -> bool:
    """Já existe consulta aprovada para o termo (ex.: "Títulos inadimplentes"): os parâmetros dela (data de
    corte, tolerância, baixa parcial...) são a definição e o fluxo de parâmetros pergunta o que faltar."""
    try:
        from .contratos import OBJETIVOS
        return any(radical in _norm(o.nome) for o in OBJETIVOS.values())
    except Exception:
        return False


def _ja_perguntado(conversa, termo) -> bool:
    ultima = conversa.mensagens.filter(papel="ia").order_by("-criado_em", "-id").first()
    return ultima is not None and (ultima.ficha or {}).get("definicao_pedida") == termo


def pergunta(user, conversa, texto) -> tuple[str, str] | None:
    """(termo, texto da pergunta) quando falta definição de negócio para responder; senão None."""
    t = _norm(texto)
    if _DEFINIDO.search(t):
        return None
    for ind in _indefinidos():
        if len(ind.radical) < 5 or not re.search(rf"\b{re.escape(ind.radical)}", t):
            continue
        if _ja_perguntado(conversa, ind.termo) or _na_memoria(user, ind.radical) or _consulta_define(ind.radical):
            continue
        return ind.termo, (f"Antes de consultar, preciso saber o que você considera “{ind.termo.lower()}”: ainda não há "
                           f"uma definição aprovada na empresa e eu não quero supor um critério. Me diga: "
                           f"{ind.perguntar}.")
    return None
