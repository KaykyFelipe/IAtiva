"""Verifica os documentos de `knowledge/`: cabeçalho, aprovação e conteúdo proibido.

Mostra só caminho, situação e problemas (nunca o conteúdo). Sai com erro se houver problema.
"""
from django.core.management.base import BaseCommand, CommandError

from iativa.knowledge.documentos import carregar_todos


class Command(BaseCommand):
    help = "Valida os documentos de conhecimento (knowledge/**/*.md)."

    def handle(self, *args, **options):
        docs = carregar_todos()
        com_problema = 0
        for d in docs:
            estado = "recuperável" if d.recuperavel else d.situacao or "sem situação"
            self.stdout.write(f"{d.caminho}: {estado} (v{d.versao or '?'}, {len(d.secoes)} seções)")
            for p in d.problemas:
                self.stdout.write(f"  - {p}")
            com_problema += bool(d.problemas)
        self.stdout.write(f"documentos={len(docs)} com_problema={com_problema}")
        if com_problema:
            raise CommandError("Há documentos com problemas.")
