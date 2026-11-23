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

_FASES_PUBLICAS = (
    (("Lendo o pedido", "Decidindo o que fazer"), "Entendendo o que você pediu…"),
    (("Preparando a consulta", "Usando a receita", "Executando um passo da receita"),
     "Organizando os critérios para a consulta…"),
    (("Procurando no dicionário", "Montando a consulta nova", "Criando a consulta", "A tabela ",
      "Nada no nome"), "Procurando a fonte mais adequada para responder…"),
    (("Calculando o total no banco",), "Calculando os totais dos dados encontrados…"),
    (("Consultando o ERP", "Localizando as matrículas", "Consultando alterações", "Calculando "),
     "Buscando as informações que correspondem ao seu pedido…"),
    (("Conferindo se as junções", "Conferindo no dicionário", "Conferindo se a resposta",
      "Lista pronta", "Verificação de toda a lista"),
     "Conferindo os registros para montar a resposta…"),
    (("A consulta não respondeu a tempo", "A consulta conhecida encontrou uma falha temporária"),
     "A consulta demorou mais que o esperado. Tentando novamente…"),
    (("Ajustando o caminho", "O critério usado", "A mesma ação", "A resposta não atendia",
      "Etapa concluída", "Uma pergunta respondida", "Investigando divergência"),
     "Ajustando a busca para completar o seu pedido…"),
    (("Escrevendo a resposta", "Resposta pronta", "Aprendi como responder"),
     "Organizando o resultado para mostrar aqui…"),
)


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
    estado = ler(usuario_id, pedido)
    for passo in reversed(estado["passos"]):
        for prefixos, mensagem in _FASES_PUBLICAS:
            if passo.startswith(prefixos):
                return {"mensagem": mensagem, "fim": estado["fim"]}
    return {"mensagem": "", "fim": estado["fim"]}
