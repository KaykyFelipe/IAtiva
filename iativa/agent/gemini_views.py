from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.shortcuts import render
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST

from . import claude_views, conexoes, gemini_cli
from .claude_login import permitido
from .provedores import FalhaModelo


@require_POST
@sensitive_post_parameters('chave')
def conta(request):
    if not permitido(request.user) or request.POST.get('perfil') != 'atendimento':
        raise PermissionDenied
    mensagem, erro, status = '', '', 200
    try:
        acao = request.POST.get('acao')
        if acao == 'conectar':
            gemini_cli.conectar(request.user, request.POST.get('chave', ''), request.POST.get('modelo', ''))
            mensagem = 'Conexão Gemini verificada e salva.'
        elif acao == 'usar':
            conexoes.escolher(request.user, 'atendimento', 'gemini')
            mensagem = 'Gemini está em uso no atendimento.'
        elif acao == 'desconectar':
            gemini_cli.desconectar(request.user)
            mensagem = 'Conexão Gemini removida.'
        else:
            erro, status = 'Ação inválida.', 400
    except (FalhaModelo, ImproperlyConfigured) as exc:
        erro = str(exc)
    ctx = claude_views._contexto(request)
    for cartao in ctx['cartoes']:
        if cartao['perfil'] == 'atendimento':
            cartao['gemini'].update(mensagem=mensagem, erro=erro)
    return render(request, 'agent/claude_conta.html', ctx, status=status)
