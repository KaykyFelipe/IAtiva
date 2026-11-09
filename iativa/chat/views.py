"""Chat mínimo da Etapa 1.

A mensagem é TRANSITÓRIA: é validada, confirmada como recebida e descartada ao fim da
requisição. Não é gravada no banco, em sessão, cache, arquivo ou log. A interpretação
será implementada na Etapa 4.
"""
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .forms import MessageForm

ESTADO_RECEBIDA = "recebida"


@require_http_methods(["GET", "POST"])
def index(request):
    resposta = None
    if request.method == "POST":
        form = MessageForm(request.POST)
        if form.is_valid():
            resposta = {
                "estado": ESTADO_RECEBIDA,
                "recebida_em": timezone.localtime(),
                "caracteres": len(form.cleaned_data["mensagem"]),
            }
            form = MessageForm()
    else:
        form = MessageForm()

    context = {"form": form, "resposta": resposta}
    if request.headers.get("HX-Request") == "true":
        return render(request, "chat/_painel.html", context)
    return render(request, "chat/index.html", context)
