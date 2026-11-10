"""Descarta o que passou do prazo de retenção (D11, PLANO.md §4).

- Valores de parâmetros com dado pessoal: removidos em `expira_em` (30 dias).
- Solicitações encerradas há mais de `solicitacao_dias` (90): removidas com seus parâmetros.
- Conversas sem solicitações e paradas há mais de `solicitacao_dias`: removidas.
- Auditoria: somente se `auditoria_dias` estiver definido (prazo ainda "a definir").

Imprime apenas contagens. Deve ser agendado diariamente no servidor.
"""
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from iativa.audit.models import OperationAudit
from iativa.audit.servicos import registrar
from iativa.chat.models import ConversationContext, RequestContext
from iativa.memory.models import ParameterBinding


class Command(BaseCommand):
    help = "Remove dados que passaram do prazo de retenção (imprime só contagens)."

    def add_arguments(self, parser):
        parser.add_argument("--simular", action="store_true", help="Só conta, sem remover.")

    def handle(self, *args, simular=False, **options):
        cfg = settings.IATIVA_RETENCAO
        agora = timezone.now()
        limite = agora - timedelta(days=int(cfg["solicitacao_dias"]))

        alvos = {
            "valores_dado_pessoal": ParameterBinding.objects.filter(expira_em__lte=agora),
            "solicitacoes": RequestContext.objects.filter(encerrado_em__lte=limite),
            "conversas": ConversationContext.objects.annotate(n=Count("solicitacoes")).filter(
                n=0, atualizado_em__lte=limite
            ),
        }
        if cfg.get("auditoria_dias"):
            alvos["auditoria"] = OperationAudit.objects.filter(
                criado_em__lte=agora - timedelta(days=int(cfg["auditoria_dias"]))
            )

        contagens = {}
        with transaction.atomic():
            for nome, qs in alvos.items():
                if simular:
                    contagens[nome] = qs.count()
                    continue
                if nome == "conversas":
                    # Conta de novo após remover solicitações (conversas podem ter ficado vazias).
                    qs = ConversationContext.objects.annotate(n=Count("solicitacoes")).filter(
                        n=0, atualizado_em__lte=limite
                    )
                    qs = ConversationContext.objects.filter(pk__in=list(qs.values_list("pk", flat=True)))
                contagens[nome] = qs.count()
                qs.delete()
            if not simular:
                registrar("expurgo", "retencao", linhas=sum(contagens.values()))

        prefixo = "Simulação" if simular else "Expurgo"
        resumo = ", ".join(f"{k}={v}" for k, v in contagens.items())
        self.stdout.write(f"{prefixo}: {resumo}")
