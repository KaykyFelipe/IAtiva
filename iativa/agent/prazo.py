"""Prazo total de uma mensagem (M24).

O ciclo do agente define um prazo; cada chamada ao modelo e cada consulta ao ERP usa no máximo o
tempo que ainda resta. Fora de uma mensagem (sem prazo definido) os tempos configurados valem.
"""
import time
from contextlib import contextmanager
from contextvars import ContextVar

_PRAZO: ContextVar[float | None] = ContextVar("iativa_prazo", default=None)


class PrazoEsgotado(RuntimeError):
    """O tempo total da mensagem acabou antes de chamar o modelo ou consultar o ERP."""


def agora() -> float:
    return time.monotonic()


@contextmanager
def definir(segundos: float):
    token = _PRAZO.set(agora() + max(0.0, float(segundos)))
    try:
        yield
    finally:
        _PRAZO.reset(token)


def restante() -> float | None:
    fim = _PRAZO.get()
    return None if fim is None else fim - agora()


def esgotado() -> bool:
    r = restante()
    return r is not None and r <= 0


def exigir():
    if esgotado():
        raise PrazoEsgotado("Prazo total da mensagem esgotado.")


def limitar(segundos):
    """Timeout de uma operação: o configurado ou o que resta do prazo, o que for menor (mínimo 1 s)."""
    r = restante()
    if r is None:
        return segundos
    exigir()
    return max(1, min(int(segundos), int(r) or 1))
