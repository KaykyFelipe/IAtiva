"""M125: o contrato de entrega fica fiel ao pedido, qualquer que seja a leitura do modelo.

O modelo (1ª ou 2ª leitura) pode transformar um pedido em várias entregas que a pessoa não pediu, e o ciclo,
o revisor e o ensaio da manutenção passam a cobrar todas. Duas regras determinísticas, sem tabela ou frase
fixa de um caso:

1. Interseção: "X e Y no mesmo ano/mês/período", "ambos", "os dois" pedem UMA entrega, os registros com os
   dois eventos. Contagens de cada evento em separado são outras entregas e saem do contrato.
2. Lista só quando pedida: "quais", "quem", "liste", "nomes", "detalhe" (ou preferência guardada na memória da
   pessoa sobre o mesmo assunto). Pedido de quantidade sem isso não ganha lista nominal de brinde.
"""
import re

from .periodo import _norm

INTERSECAO = re.compile(
    r'\b(?:n?[oa]s?|d[oa]s?|em)\s+mesm[oa]s?\s+(?:ano|mes|periodo|dia|semana|data|vinculo|registro|contrato|pedido)\b'
    r'|\bambos\b|\bambas\b|\bos dois\b|\bas duas\b|\bsimultaneamente\b|\bao mesmo tempo\b')
# Dois eventos ligados por "e": "admitidos e desligados", "admissão e desligamento", "faturados e entregues".
_EVENTO = r'[a-z]{3,}?(?:ad|id|ert|eit|ost|ag|gu)[oa]s?|[a-z]{3,}gues|[a-z]{4,}(?:cao|sao|mento|tura)s?'
_PAR = re.compile(rf'\b({_EVENTO})\s+e\s+(?:tambem\s+)?({_EVENTO})\b')
_CONTA = re.compile(r'\b(quantos|quantas|quantidade|contagem|numero de|total de)\b')
_LISTA = re.compile(r'\b(lista\w*|listagem|nomes?|quem|relacao|detalh\w*|identifica\w*)\b')
_AGRUPAMENTO = re.compile(r'\b(?:por|cada)\s+(filia\w*|mes|ano|cliente|setor|cargo|funcao|status)\b')


def eventos_intersecao(texto: str) -> tuple[str, ...]:
    """Raízes (4 letras) dos eventos cuja interseção foi pedida; () quando o pedido não é uma interseção."""
    t = _norm(texto)
    if not INTERSECAO.search(t):
        return ()
    m = _PAR.search(t)
    if not m or m.group(1)[:4] == m.group(2)[:4]:
        return ()
    return m.group(1)[:4], m.group(2)[:4]


def lista_pedida(user, texto: str) -> bool:
    from . import contagem
    if contagem.so_numero(texto):
        return False
    if contagem._PEDE_DETALHE.search(_norm(texto)):
        return True
    # Preferência guardada vale para a contagem simples do assunto ("admitidos no mês"), não para um agregado
    # por grupo ("por filial") nem para a interseção de dois eventos: a pessoa pediu outra coisa.
    return (contagem.solicitado(texto) and not eventos_intersecao(texto)
            and contagem.preferencia_detalhe(user, texto))


def _cita(descricao: str, raiz: str) -> bool:
    return any(w.startswith(raiz) for w in re.findall(r'[a-z]+', _norm(descricao)))


def _e_lista(e: dict) -> bool:
    return e.get('tipo') == 'consulta' or bool(_LISTA.search(_norm(e.get('descricao', ''))))


def _e_contagem(e: dict) -> bool:
    return e.get('tipo') in ('calculo', 'verificacao') or e.get('metrica') == 'contagem'


def ajustar(texto: str, entregas: list, user=None) -> list:
    """Contrato fiel ao pedido (regras no topo do módulo). Devolve uma lista nova; não altera a recebida."""
    from .contagem import entregas_separadas
    if separadas := entregas_separadas(texto):
        return separadas
    itens = [dict(e) for e in entregas or [] if isinstance(e, dict)]
    if not itens:
        return itens
    t = _norm(texto)
    eventos = eventos_intersecao(texto)
    if eventos:
        contagens = [e for e in itens if _e_contagem(e) and not _e_lista(e)]
        ambos = [e for e in contagens if all(_cita(e.get('descricao', ''), r) for r in eventos)]
        if _CONTA.search(t) and not ambos:
            ambos = [_entrega_intersecao(t)]
        # Uma contagem da interseção; contagens de um evento só (e repetições) saem; listas seguem a regra 2.
        itens = ambos[:1] + [e for e in itens if e not in contagens]
    if _CONTA.search(t) and not lista_pedida(user, texto):
        sem_lista = [e for e in itens if not _e_lista(e)]
        if sem_lista:
            itens = sem_lista
    return itens


def _entrega_intersecao(t: str) -> dict:
    m = _PAR.search(t)
    qual = INTERSECAO.search(t)
    desc = f'Contagem dos registros {m.group(1)} e {m.group(2)} {qual.group(0)}'
    ano = re.search(r'\bem ([12]\d{3})\b', t)
    if ano:
        desc += f' em {ano.group(1)}'
    grupo = _AGRUPAMENTO.search(t)
    if grupo:
        desc += f', {grupo.group(0)}'
    return {'descricao': desc[:200], 'tipo': 'calculo', 'metrica': 'contagem'}


def remapear_cumpridas(anterior: list, novo: list, cumpridas: list) -> list:
    """Índices cumpridos do contrato antigo que continuam no novo (mesma entrega)."""
    return [novo.index(anterior[i]) for i in cumpridas if i < len(anterior) and anterior[i] in novo]
