"""Reconstrói o Markdown de todas as receitas aprendidas a partir do catálogo (M95)."""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Gera/reconstrói os Markdown das receitas aprendidas (IATIVA_RECEITAS_DIR)."

    def handle(self, *args, **opts):
        from iativa.agent import receitas
        n = receitas.reconstruir()
        self.stdout.write(f"{n} receita(s) gerada(s) em {receitas.pasta()}")
