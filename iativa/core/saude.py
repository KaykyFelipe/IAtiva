"""Saúde, versão e drenagem para a atualização sem interrupção (M31).

- `/saude/pronto/`: a instância só recebe tráfego se responder 200 aqui (banco acessível e nenhuma
  migração pendente para o código desta versão).
- `/saude/ativas/`: quantas requisições esta instância está atendendo agora (drenagem antes de parar).
- Cabeçalho `X-IAtiva-Versao` em toda resposta: identifica a versão que atendeu.

Nada aqui expõe dados: só versão, contagens e "pronto/não pronto".
"""
import os
import threading
import time

from django.contrib.auth.decorators import login_not_required
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.http import JsonResponse
from django.views.decorators.http import require_GET

_ATIVAS = 0
_TRAVA = threading.Lock()


def versao() -> str:
    return os.environ.get("IATIVA_VERSAO", "dev")


def ativas() -> int:
    with _TRAVA:
        return _ATIVAS


class VersaoEContadorMiddleware:
    """Conta as requisições em andamento (menos as de saúde) e marca a versão na resposta."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        global _ATIVAS
        conta = not request.path.startswith("/saude/")
        if conta:
            with _TRAVA:
                _ATIVAS += 1
        try:
            response = self.get_response(request)
        finally:
            if conta:
                with _TRAVA:
                    _ATIVAS -= 1
        response["X-IAtiva-Versao"] = versao()
        return response


def _migracoes_pendentes() -> int:
    executor = MigrationExecutor(connection)
    return len(executor.migration_plan(executor.loader.graph.leaf_nodes()))


@login_not_required
@require_GET
def pronto(request):
    if os.environ.get("IATIVA_FORCAR_NAO_PRONTO") == "1" and _teste_ligado():
        return JsonResponse({"versao": versao(), "pronto": False, "motivo": "forçado (teste)"}, status=503)
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
        pendentes = _migracoes_pendentes()
    except Exception as exc:  # sem detalhes: podem conter dados de conexão
        return JsonResponse({"versao": versao(), "pronto": False, "motivo": type(exc).__name__}, status=503)
    if pendentes:
        return JsonResponse({"versao": versao(), "pronto": False, "motivo": f"{pendentes} migração(ões) pendente(s)"},
                            status=503)
    return JsonResponse({"versao": versao(), "pronto": True})


@login_not_required
@require_GET
def em_andamento(request):
    return JsonResponse({"versao": versao(), "ativas": ativas()})


def _teste_ligado() -> bool:
    """Endpoints de teste só existem no ambiente de teste de implantação, nunca com o ERP real."""
    from django.conf import settings

    return os.environ.get("IATIVA_TESTE_IMPLANTACAO") == "1" and settings.IATIVA_METADADOS.get("fonte") == "sintetico"


@login_not_required
@require_GET
def lenta(request):
    """Só no teste de implantação: simula uma consulta longa (conta como requisição ativa)."""
    from django.http import Http404

    if not _teste_ligado():
        raise Http404
    segundos = min(max(float(request.GET.get("s", "5")), 0), 120)
    time.sleep(segundos)
    return JsonResponse({"versao": versao(), "dormiu": segundos})
