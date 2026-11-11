"""Testa o provedor de IA configurado, sem dados do ERP.

    python manage.py testar_ia            # lista modelos Flash da chave e faz uma pergunta de teste
    python manage.py testar_ia --modelos  # só lista os modelos
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from iativa.agent.decisao import EntradaAgente
from iativa.agent.provedores import FalhaModelo, obter_provedor


class Command(BaseCommand):
    help = "Testa o provedor de IA (Gemini) com uma mensagem de exemplo, sem dados do ERP."

    def add_arguments(self, parser):
        parser.add_argument("--modelos", action="store_true")

    def handle(self, *args, modelos=False, **options):
        cfg = settings.IATIVA_AGENTE
        self.stdout.write(f"Provedor: {cfg['provedor']} | modelo: {cfg.get('gemini_modelo')} | "
                          f"envio liberado: {cfg.get('envio_liberado')} | chave: {'sim' if cfg.get('gemini_api_key') else 'não'}")
        if cfg["provedor"] != "gemini":
            raise CommandError("IATIVA_AGENTE_PROVEDOR não é gemini.")
        try:
            provedor = obter_provedor()
        except Exception as exc:
            raise CommandError(str(exc)) from None
        try:
            nomes = sorted(m.name.removeprefix("models/") for m in provedor.cliente.models.list()
                           if "flash" in (m.name or ""))
        except Exception as exc:
            raise CommandError(f"Não consegui listar modelos ({type(exc).__name__}). Chave inválida?") from None
        self.stdout.write("Modelos Flash da chave: " + (", ".join(nomes) or "(nenhum)"))
        if provedor.modelo not in nomes:
            self.stdout.write(self.style.WARNING(f"O modelo configurado ({provedor.modelo}) não está na lista."))
        if modelos:
            return
        entrada = EntradaAgente(
            mensagem="Quais clientes estão inadimplentes até 31/12/2025 na filial 001001?", hoje="2026-01-15",
            historico=[], solicitacao_atual=None, administrador=False,
            objetivos=[{"codigo": "titulos-em-atraso", "nome": "Títulos em atraso", "descricao": "",
                        "parametros": [{"codigo": "data-corte", "nome": "Data de corte", "descricao": "",
                                        "tipo": "data", "opcoes": []},
                                       {"codigo": "filial-analise", "nome": "Filial", "descricao": "",
                                        "tipo": "filial", "opcoes": ["001001"]}]}],
        )
        try:
            saida = provedor.decidir(entrada)
        except FalhaModelo as exc:
            raise CommandError(f"Falha no teste: {exc}") from None
        self.stdout.write(self.style.SUCCESS(f"OK: ação={saida.get('acao')} objetivo={saida.get('objetivo')} "
                                             f"parametros={saida.get('parametros')}"))
        self.stdout.write(f"Resposta: {saida.get('resposta')}")
