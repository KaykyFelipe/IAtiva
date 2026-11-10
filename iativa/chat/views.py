"""Chat (Etapa 4): interpretação, esclarecimentos e situação da solicitação.

A mensagem é TRANSITÓRIA: vai ao orquestrador (e ao provedor de IA) e é descartada ao fim da
requisição. Não é gravada no banco, sessão, cache, arquivo ou log, nem ecoada na resposta.
A sessão guarda só o número da solicitação em andamento.
"""
from django.shortcuts import render
from django.views.decorators.http import require_http_methods, require_POST

from iativa.agent import orquestrador
from iativa.chat.servicos import cancelar
from iativa.memory.models import TipoValor

from .forms import MessageForm

CHAVE_SESSAO = "solicitacao_atual"


def _render(request, resposta=None, form=None):
    if resposta is not None:
        s = resposta.solicitacao
        if s is not None and s.estado not in ("cancelada", "concluida"):
            request.session[CHAVE_SESSAO] = s.pk
        else:
            request.session.pop(CHAVE_SESSAO, None)
    atual = orquestrador.solicitacao_ativa(request.user, request.session.get(CHAVE_SESSAO))
    context = {"form": form or MessageForm(), "resposta": resposta, "atual": atual, "tipos": TipoValor}
    if request.headers.get("HX-Request") == "true":
        return render(request, "chat/_painel.html", context)
    return render(request, "chat/index.html", context)


@require_http_methods(["GET", "POST"])
def index(request):
    if request.method == "GET":
        return _render(request)
    form = MessageForm(request.POST)
    if not form.is_valid():
        return _render(request, form=form)
    resposta = orquestrador.processar_mensagem(
        request.user, form.cleaned_data["mensagem"], request.session.get(CHAVE_SESSAO)
    )
    return _render(request, resposta)


@require_POST
def parametro(request):
    codigo = request.POST.get("codigo", "")[:60]
    if "inicio" in request.POST or "fim" in request.POST:
        valor = orquestrador.valor_periodo(request.POST.get("inicio", ""), request.POST.get("fim", ""))
    else:
        valor = request.POST.get("valor", "")[:200]
    resposta = orquestrador.responder_parametro(request.user, request.session.get(CHAVE_SESSAO), codigo, valor)
    return _render(request, resposta)


@require_POST
def cancelar_atual(request):
    s = orquestrador.solicitacao_ativa(request.user, request.session.get(CHAVE_SESSAO))
    resposta = None
    if s is not None:
        resposta = orquestrador.Resposta("cancelada", "Solicitação cancelada.", solicitacao=cancelar(request.user, s))
    request.session.pop(CHAVE_SESSAO, None)
    return _render(request, resposta)
