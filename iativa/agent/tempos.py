"""M125: quanto tempo cada etapa de um pedido levou (milissegundos), para achar onde o prazo de 5 minutos vai.

Etapas: leitura (1ª e 2ª interpretação do pedido), decisao (chamadas do modelo no ciclo), erp (execução das
consultas), revisao (conferência da resposta) e total. Vai na ficha da resposta (`tempos_ms`) e no log; nunca
texto do pedido, dados ou SQL. As etapas da manutenção (agente, testes, ensaio, publicação) ficam no log do
executor; `manage.py tempos_pedido` junta tudo de um pedido.
"""
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar

_ATUAL: ContextVar[dict | None] = ContextVar("iativa_tempos", default=None)
logger = logging.getLogger("iativa.tempos")


@contextmanager
def pedido(rotulo: str = "pedido"):
    """Abre a contagem de um pedido (ou continuação). Reaproveita a que já estiver aberta."""
    if _ATUAL.get() is not None:
        yield _ATUAL.get()
        return
    d: dict = {}
    token = _ATUAL.set(d)
    inicio = time.monotonic()
    try:
        yield d
    finally:
        d["total"] = int((time.monotonic() - inicio) * 1000)
        _ATUAL.reset(token)
        logger.info("tempos %s %s", rotulo, " ".join(f"{k}={v}ms" for k, v in d.items()))


@contextmanager
def medir(etapa: str):
    d = _ATUAL.get()
    inicio = time.monotonic()
    try:
        yield
    finally:
        if d is not None:
            d[etapa] = d.get(etapa, 0) + int((time.monotonic() - inicio) * 1000)
