from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.shortcuts import render
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET, require_http_methods

from .claude_login import (
    ativa, chave_sessao, conectado, login, permitido, provedor_login,
)
from .provedores import FalhaModelo


def _autorizar(request):
    if not permitido(request.user):
        raise PermissionDenied


def _contexto(request, estado=None, erro=""):
    return {"estado": estado, "erro": erro, "claude_ativo": ativa(request.user)}


@require_http_methods(["GET", "POST"])
@sensitive_post_parameters("codigo")
def conta(request):
    _autorizar(request)
    estado, erro = None, ""
    if request.method == "POST":
        try:
            sessao = chave_sessao(request)
            acao = request.POST.get("acao")
            if acao == "conectar":
                estado = login.iniciar(request.user, sessao)
            elif acao == "concluir":
                estado = login.concluir(request.user, sessao, request.POST.get("fluxo", ""), request.POST.get("codigo", ""))
            elif acao == "cancelar":
                estado = login.cancelar(request.user, sessao, request.POST.get("fluxo", ""))
            elif acao == "desconectar":
                login.desconectar(request.user, sessao)
                estado = {"fase": "desconectado", "mensagem": "Claude desconectado deste IAtiva."}
            elif acao == "verificar":
                provider = provedor_login(request.user)
                provider.checar_cli()
                ok = conectado(provider)
                estado = {"fase": "conectado" if ok else "desconectado",
                          "mensagem": "Login Claude confirmado." if ok else "Nenhuma conta Claude conectada neste IAtiva."}
            else:
                return render(request, "agent/claude_conta.html", _contexto(request, erro="Ação inválida."), status=400)
        except (FalhaModelo, ImproperlyConfigured) as exc:
            erro = str(exc)
            if request.POST.get("fluxo"):
                try:
                    estado = login.obter(request.user, sessao, request.POST["fluxo"]).estado()
                except FalhaModelo:
                    pass
    return render(request, "agent/claude_conta.html", _contexto(request, estado, erro))


@require_GET
def estado(request, fluxo_id):
    _autorizar(request)
    try:
        atual = login.obter(request.user, chave_sessao(request), str(fluxo_id)).estado()
        ctx = _contexto(request, atual)
    except FalhaModelo as exc:
        ctx = _contexto(request, erro=str(exc))
    return render(request, "agent/_claude_estado.html", ctx)
