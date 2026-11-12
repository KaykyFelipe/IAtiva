"""Andamento ao vivo do "Pensando…" (M26).

Enquanto o chat processa uma mensagem, cada etapa avisa o que está fazendo ("Decidindo o que
fazer", "Consultando o ERP — Matriz", "Calculando o total no banco"…). O navegador lê esses avisos
a cada fração de segundo e troca o texto do "Pensando…".

Só textos operacionais: nunca linhas, nomes, números ou SQL do ERP. Ficam só na memória do processo
web (não vão a banco, cache nem log) e somem alguns minutos depois. Exige um único processo web,
como o resto do chat (runserver --noreload).
"""
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar

_ATUAL: ContextVar[tuple | None] = ContextVar("iativa_andamento", default=None)
_PASSOS: dict = {}
_TRAVA = threading.Lock()
VALIDADE_S = 600
MAX_PASSOS = 20


def _limpar(agora):
    for k in [k for k, v in _PASSOS.items() if agora - v["t"] > VALIDADE_S]:
        del _PASSOS[k]


@contextmanager
def acompanhar(usuario_id, pedido):
    if not pedido:
        yield
        return
    chave = (usuario_id, str(pedido))
    agora = time.monotonic()
    with _TRAVA:
        _limpar(agora)
        _PASSOS[chave] = {"passos": [], "fim": False, "t": agora}
    token = _ATUAL.set(chave)
    try:
        yield
    finally:
        _ATUAL.reset(token)
        with _TRAVA:
            if chave in _PASSOS:
                _PASSOS[chave]["fim"] = True


def avisar(texto: str):
    chave = _ATUAL.get()
    if chave is None or not texto:
        return
    texto = " ".join(str(texto).split())[:160]
    with _TRAVA:
        item = _PASSOS.get(chave)
        if item is None or (item["passos"] and item["passos"][-1] == texto):
            return
        item["passos"] = (item["passos"] + [texto])[-MAX_PASSOS:]
        item["t"] = time.monotonic()


def ler(usuario_id, pedido) -> dict:
    with _TRAVA:
        item = _PASSOS.get((usuario_id, str(pedido)))
        return {"passos": list(item["passos"]), "fim": item["fim"]} if item else {"passos": [], "fim": False}
