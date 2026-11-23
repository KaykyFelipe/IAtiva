"""Andamento ao vivo do "Pensando…" (M26).

Enquanto o chat processa uma mensagem, cada etapa registra seu andamento. A interface recebe
somente uma mensagem resumida e previamente definida para a fase atual.

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
MAX_PASSOS = 80

# M126: a tela mostra o passo REAL (consulta, filial, tabela, cálculo), não uma frase fixa por fase. Só os
# avisos internos ganham redação para a pessoa; a parte específica (nome da consulta, filial) é mantida.
_REESCRITA = (
    ("Decidindo o que fazer", "Analisando o pedido para escolher a consulta"),
    ("Lendo o pedido e o histórico da conversa", "Lendo o seu pedido e o histórico da conversa"),
    ("Ajustando o caminho: ", "Ajustando a busca: "),
    ("A mesma ação já foi feita", "Esse caminho já foi tentado; mudando de abordagem"),
    ("A resposta só anunciava a ação", "Executando a ação anunciada"),
    ("Etapa concluída; ainda falta parte do pedido", "Uma parte pronta; seguindo para a próxima"),
    ("Escrevendo a resposta", "Montando a resposta"),
)
MAX_PUBLICO = 160
# Avisos do próprio sistema. Texto livre do modelo (o que ele anuncia) não vai para a tela.
_DO_SISTEMA = tuple(p for p, _ in _REESCRITA) + (
    "A consulta", "A resposta não atendia", "A soma inicial", "A tabela ", "Aprendi como", "Calculando",
    "Conferindo", "Consultando", "Criando a consulta", "Executando um passo da receita", "Falta uma definição",
    "Guardando na memória", "Investigando divergência", "Localizando", "Montando a consulta", "Nada no nome",
    "Não tenho isso ainda", "O critério usado", "O pedido envolve", "Preparando a consulta", "Procurando",
    "Resposta pronta", "Retomando", "Uma pergunta respondida", "Usando a receita", "Verificação de toda")


def _publico(passo: str) -> str:
    """Passo do sistema na redação da tela; '' para texto que não é do sistema. Nome interno de consulta (entre
    aspas) e código de tabela entre parênteses saem; filial, período e o tipo de ação ficam."""
    import re
    if not passo.startswith(_DO_SISTEMA):
        return ""
    passo = re.sub(r"\s*“[^”]*”", "", passo)
    passo = re.sub(r"\s*\([A-Z][A-Z0-9]{2}\)", "", passo)
    passo = re.sub(r"(?<=Montando a consulta nova com os campos de )[A-Z0-9, ]+", "outra tabela", passo)
    for prefixo, texto in _REESCRITA:
        if passo.startswith(prefixo):
            resto = passo[len(prefixo):] if prefixo.endswith(": ") else ""
            passo = texto + resto
            break
    passo = passo.split(". ")[0].rstrip(".;")
    return passo if len(passo) <= MAX_PUBLICO else passo[:MAX_PUBLICO - 1].rstrip() + "…"


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
    texto = " ".join(str(texto).split())[:400]  # M108: frase inteira (em 160 cortava no meio)
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


def estado_publico(usuario_id, pedido):
    """O passo atual como ele é. Se a mesma ação volta depois de outras (nova tentativa), diz qual tentativa é,
    em vez de repetir o mesmo nome de etapa."""
    estado = ler(usuario_id, pedido)
    passos = [p for p in (_publico(p) for p in estado["passos"]) if p]
    if not passos:
        return {"mensagem": "", "fim": estado["fim"]}
    atual = passos[-1]
    vezes = sum(1 for k, p in enumerate(passos) if p == atual and (k == 0 or passos[k - 1] != p))
    return {"mensagem": atual + (f" (tentativa {vezes})" if vezes > 1 else "") + "…", "fim": estado["fim"]}
