"""Máquina de estados da solicitação (PLANO.md §3, princípio 3).

A liberação para execução depende de checagens determinísticas do backend
(`servicos.avaliar_completude`), nunca da confiança declarada pelo modelo.
"""
from .models import EstadoSolicitacao as E

TRANSICOES: dict[str, frozenset[str]] = {
    E.RASCUNHO: frozenset({E.AGUARDANDO_PARAMETROS, E.VALIDANDO, E.CANCELADA}),
    E.AGUARDANDO_PARAMETROS: frozenset({E.VALIDANDO, E.CANCELADA}),
    E.VALIDANDO: frozenset({E.PRONTA, E.AGUARDANDO_PARAMETROS, E.BLOQUEADA, E.CANCELADA}),
    E.PRONTA: frozenset({E.EXECUTANDO, E.AGUARDANDO_PARAMETROS, E.CANCELADA}),
    E.EXECUTANDO: frozenset({E.CONCLUIDA, E.NAO_VALIDADA, E.FALHOU}),
    # Resultado não validado exige esclarecimento antes de refazer a consulta.
    E.NAO_VALIDADA: frozenset({E.AGUARDANDO_PARAMETROS, E.CANCELADA}),
    E.FALHOU: frozenset({E.AGUARDANDO_PARAMETROS, E.CANCELADA}),
    E.BLOQUEADA: frozenset({E.VALIDANDO, E.AGUARDANDO_PARAMETROS, E.CANCELADA}),
    # Concluída e cancelada são finais. Reagrupar ou reabrir = nova solicitação e nova execução.
    E.CONCLUIDA: frozenset(),
    E.CANCELADA: frozenset(),
}

FINAIS = frozenset({E.CONCLUIDA, E.CANCELADA})
# Estados em que a solicitação ainda está em preparação (aceita parâmetros e pode ser cancelada).
EM_PREPARACAO = frozenset(
    {E.RASCUNHO, E.AGUARDANDO_PARAMETROS, E.VALIDANDO, E.PRONTA, E.NAO_VALIDADA, E.FALHOU, E.BLOQUEADA}
)


class TransicaoInvalida(Exception):
    pass


def pode_transicionar(atual: str, novo: str) -> bool:
    return novo in TRANSICOES.get(atual, frozenset())
