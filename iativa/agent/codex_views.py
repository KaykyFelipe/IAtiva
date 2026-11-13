from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_http_methods

from . import claude_views, codex_login, conexoes
from .claude_login import permitido, perfil_valido, chave_sessao
from .provedores import FalhaModelo


def contexto(request, perfil, estado=None, erro=''):
    ctx = claude_views._contexto(request)
    for c in ctx['cartoes']:
        if c['perfil'] == perfil:
            c['chatgpt'].update(estado=estado, erro=erro)
    return ctx


@require_http_methods(['GET', 'POST'])
def conta(request):
    if not permitido(request.user):
        raise PermissionDenied
    if request.method == 'GET':
        return render(request, 'agent/claude_conta.html', claude_views._contexto(request))
    perfil = request.POST.get('perfil', 'atendimento')
    try:
        perfil_valido(perfil)
    except FalhaModelo as exc:
        return render(request, 'agent/claude_conta.html', contexto(request, 'atendimento', erro=str(exc)), status=400)
    atual, erro = None, ''
    try:
        acao = request.POST.get('acao')
        if acao == 'conectar':
            atual = codex_login.login.iniciar(request.user, chave_sessao(request), perfil)
        elif acao == 'cancelar':
            atual = codex_login.login.cancelar(request.user, chave_sessao(request), request.POST.get('fluxo', ''), perfil)
        elif acao == 'desconectar':
            codex_login.login.desconectar(request.user, chave_sessao(request), perfil)
            atual = {'fase': 'desconectado', 'mensagem': 'ChatGPT desconectado deste agente.'}
        elif acao == 'verificar':
            ok = codex_login.verificar(request.user, perfil)
            atual = {'fase': 'conectado' if ok else 'desconectado',
                     'mensagem': 'Login ChatGPT confirmado.' if ok else 'Nenhuma conta ChatGPT conectada neste agente.'}
        elif acao == 'modelos':
            codex_login.atualizar_modelos(request.user, perfil)
            atual = {'mensagem': 'Catálogo de modelos da conta atualizado.'}
        elif acao == 'preferencias':
            codex_login.salvar_preferencias(request.user, perfil, request.POST.get('modelo', ''), request.POST.get('esforco', ''))
            atual = {'mensagem': 'Modelo e esforço ChatGPT salvos para este agente.'}
        elif acao == 'usar':
            conexoes.escolher(request.user, perfil, request.POST.get('provedor', ''))
            nome = 'Claude' if request.POST.get('provedor') == 'claude' else 'ChatGPT'
            atual = {'mensagem': f'{nome} está em uso neste agente. A outra conexão foi preservada.'}
        else:
            return render(request, 'agent/claude_conta.html', contexto(request, perfil, erro='Ação inválida.'), status=400)
    except FalhaModelo as exc:
        erro = str(exc)
    if request.POST.get('acao') == 'usar' and request.POST.get('provedor') == 'claude':
        return render(request, 'agent/claude_conta.html', claude_views._contexto(request, perfil, atual, erro))
    return render(request, 'agent/claude_conta.html', contexto(request, perfil, atual, erro))


@require_GET
def estado(request, fluxo_id):
    if not permitido(request.user):
        raise PermissionDenied
    perfil = request.GET.get('perfil', 'atendimento')
    try:
        perfil_valido(perfil)
        atual = codex_login.login.obter(request.user, chave_sessao(request), str(fluxo_id), perfil).estado()
        c = codex_login.cartao(request.user, perfil)
        c.update(estado=atual)
        cartao = claude_views._cartao(request, perfil)
        cartao['chatgpt'] = c
        return render(request, 'agent/_chatgpt_estado.html', {'g': c, 'c': cartao, 'perfil': perfil,
                                                            'atualizar_resumo': True})
    except FalhaModelo as exc:
        return render(request, 'agent/_chatgpt_estado.html',
                      {'g': {'erro': str(exc)}, 'perfil': 'invalido'}, status=400)
