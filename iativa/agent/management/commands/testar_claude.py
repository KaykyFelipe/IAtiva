"""Diagnóstico sem banco e chamada opcional com dados inteiramente fictícios."""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError
from django.test.utils import override_settings

from iativa.agent.claude_code import ClaudeCodeProvider
from iativa.agent.decisao import EntradaAgente
from iativa.agent.provedores import FalhaModelo


class Command(BaseCommand):
    help = "Confere a CLI Claude pessoal; --enviar faz uma chamada sintética, sem ERP."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--cli")
        parser.add_argument("--config-dir")
        parser.add_argument("--modelo")
        parser.add_argument("--enviar", action="store_true")

    def handle(self, *args, **options):
        current = settings.IATIVA_AGENTE
        cfg = {**current, "claude_cli": options["cli"] or current.get("claude_cli", "claude"),
               "claude_config_dir": options["config_dir"] or current.get("claude_config_dir", ""),
               "claude_modelo": options["modelo"] or current.get("claude_modelo", "sonnet"),
               "envio_liberado": True}
        # A liberação vale apenas para a mensagem fictícia deste comando, nunca para o chat.
        try:
            with override_settings(IATIVA_AGENTE=cfg):
                provider = ClaudeCodeProvider()
                provider.verificar()
                self.stdout.write("CLI compatível; login Claude confirmado. Nenhuma chamada ao ERP.")
                self.stdout.write("Confira no Claude se uso extra pago está desativado; o login não garante custo zero.")
                if options["enviar"]:
                    result = provider.decidir(EntradaAgente(
                        mensagem="Olá! Diga que este é um teste fictício.", hoje="2026-11-11",
                        historico=[], objetivos=[{
                            "codigo": "teste-ficticio", "nome": "Teste fictício", "descricao": "Sem banco",
                            "parametros": [{"codigo": "data-corte", "nome": "Data", "tipo": "data", "opcoes": []}],
                        }], solicitacao_atual=None,
                    ))
                    self.stdout.write(self.style.SUCCESS(f"JSON validado; ação={result['acao']}."))
                else:
                    self.stdout.write("Sem envio ao modelo. Use --enviar para testar uma chamada sintética.")
        except (FalhaModelo, ImproperlyConfigured, ValueError, OSError) as exc:
            raise CommandError(str(exc)) from None
