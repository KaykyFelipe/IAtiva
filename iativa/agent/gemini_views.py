from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET, require_POST

from . import claude_views, conexoes, gemini_cli, gemini_login
from .claude_login import chave_sessao, permitido
from .provedores import FalhaModelo


def _contexto(request, fluxo=None, mensagem='', erro=''):
    ctx = claude_views._contexto(request)
    for cartao in ctx['cartoes']:
        if cartao['perfil'] == 'atendimento':
            cartao['gemini'].update(fluxo=fluxo, mensagem=mensagem, erro=erro)
            ctx['c'] = cartao
    return ctx


@never_cache
@require_POST
@sensitive_post_parameters('codigo')
def conta(request):
    if not permitido(request.user) or request.POST.get('perfil') != 'atendimento':
        raise PermissionDenied
    mensagem, erro, status, fluxo = '', '', 200, None
    sessao = chave_sessao(request)
    fluxo_id = request.POST.get('fluxo', '')
    try:
        acao = request.POST.get('acao')
        if acao == 'conectar':
            fluxo = gemini_login.iniciar(request.user, sessao, request.POST.get('projeto', ''))
        elif acao == 'concluir':
            fluxo = gemini_login.concluir(request.user, sessao, fluxo_id, request.POST.get('codigo', '').strip())
        elif acao == 'cancelar':
            fluxo = gemini_login.cancelar(request.user, sessao, fluxo_id)
        elif acao == 'modelo':
            gemini_cli.salvar_modelo(request.user, request.POST.get('modelo', ''))
            mensagem = 'Modelo Gemini verificado e salvo.'
        elif acao == 'usar':
            conexoes.escolher(request.user, 'atendimento', 'gemini')
            mensagem = 'Gemini está em uso no atendimento.'
        elif acao == 'desconectar':
            gemini_login.desconectar(request.user, sessao)
            mensagem = 'Conta Google desconectada.'
        else:
            erro, status = 'Ação inválida.', 400
    except (FalhaModelo, ImproperlyConfigured) as exc:
        erro = str(exc)
        if fluxo_id:
            try:
                fluxo = gemini_login.estado(request.user, sessao, fluxo_id)
            except FalhaModelo:
                pass
    return render(request, 'agent/claude_conta.html', _contexto(request, fluxo, mensagem, erro), status=status)


@never_cache
@require_GET
def estado(request, fluxo_id):
    if not permitido(request.user):
        raise PermissionDenied
    try:
        fluxo = gemini_login.estado(request.user, chave_sessao(request), str(fluxo_id))
    except FalhaModelo as exc:
        return render(request, 'agent/_gemini.html', _contexto(request, erro=str(exc)))
    return render(request, 'agent/_gemini.html', _contexto(request, fluxo))
