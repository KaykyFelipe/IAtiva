from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.shortcuts import render
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET, require_http_methods

from .claude_login import (
    ESFORCOS, MODELOS, PERFIS, ativa, chave_sessao, conectado, config_usuario, login, permitido, provedor_login,
    salvar_preferencias,
)
from .provedores import FalhaModelo


def _autorizar(request):
    if not permitido(request.user):
        raise PermissionDenied


def _perfil(valor):
    return valor if valor in PERFIS else "atendimento"


def _cartao(request, perfil, estado=None, erro=""):
    cfg = config_usuario(request.user, perfil)
    return {"perfil": perfil, **PERFIS[perfil], "estado": estado, "erro": erro, "claude_ativo": ativa(request.user, perfil),
            "modelo_atual": cfg["claude_modelo"], "esforco_atual": cfg["claude_esforco"]}


def _contexto(request, perfil="atendimento", estado=None, erro=""):
    """Duas conexões separadas (M33): o estado e o erro vão só para o cartão que fez a ação."""
    return {"cartoes": [_cartao(request, p, *((estado, erro) if p == perfil else (None, ""))) for p in PERFIS],
            "modelos": MODELOS, "esforcos": ESFORCOS}


@require_http_methods(["GET", "POST"])
@sensitive_post_parameters("codigo")
def conta(request):
    _autorizar(request)
    estado, erro, sessao = None, "", ""
    perfil = _perfil(request.POST.get("perfil", "atendimento"))
    if request.method == "POST":
        try:
            sessao = chave_sessao(request)
            acao = request.POST.get("acao")
            fluxo = request.POST.get("fluxo", "")
            if acao == "conectar":
                estado = login.iniciar(request.user, sessao, perfil)
            elif acao == "concluir":
                estado = login.concluir(request.user, sessao, fluxo, request.POST.get("codigo", ""), perfil)
            elif acao == "cancelar":
                estado = login.cancelar(request.user, sessao, fluxo, perfil)
            elif acao == "desconectar":
                login.desconectar(request.user, sessao, perfil)
                estado = {"fase": "desconectado", "mensagem": "Claude desconectado deste agente."}
            elif acao == "preferencias":
                salvar_preferencias(request.user, request.POST.get("modelo", ""), request.POST.get("esforco", ""), perfil)
                estado = {"fase": "preferencias", "mensagem": "Modelo e esforço salvos. Valem a partir do próximo uso."}
            elif acao == "verificar":
                provider = provedor_login(request.user, perfil)
                provider.checar_cli()
                ok = conectado(provider)
                estado = {"fase": "conectado" if ok else "desconectado",
                          "mensagem": "Login Claude confirmado." if ok else "Nenhuma conta Claude conectada neste agente."}
            else:
                return render(request, "agent/claude_conta.html", _contexto(request, perfil, erro="Ação inválida."),
                              status=400)
        except (FalhaModelo, ImproperlyConfigured) as exc:
            erro = str(exc)
            if request.POST.get("fluxo"):
                try:
                    estado = login.obter(request.user, sessao, request.POST["fluxo"], perfil).estado()
                except FalhaModelo:
                    pass
    return render(request, "agent/claude_conta.html", _contexto(request, perfil, estado, erro))


@require_GET
def estado(request, fluxo_id):
    _autorizar(request)
    perfil = _perfil(request.GET.get("perfil", "atendimento"))
    try:
        atual = login.obter(request.user, chave_sessao(request), str(fluxo_id), perfil).estado()
        c = _cartao(request, perfil, atual)
    except FalhaModelo as exc:
        c = _cartao(request, perfil, erro=str(exc))
    return render(request, "agent/_claude_estado.html", {"c": c})
