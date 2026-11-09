from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from iativa.synthetic_erp.generator import DEFAULT_SEED, generate


class Command(BaseCommand):
    help = "Gera a base SINTÉTICA do ERP (dados fictícios, determinísticos). Não toca o banco da aplicação."

    def add_arguments(self, parser):
        parser.add_argument("--semente", type=int, default=DEFAULT_SEED, help="Semente do gerador.")
        parser.add_argument(
            "--substituir", action="store_true", help="Substitui a base sintética existente."
        )

    def handle(self, *args, **options):
        path = Path(settings.SYNTHETIC_ERP_PATH)
        try:
            counts = generate(path, seed=options["semente"], replace=options["substituir"])
        except FileExistsError:
            raise CommandError(
                f"Já existe uma base sintética em {path}. Use --substituir para gerar novamente."
            )
        resumo = ", ".join(f"{tabela}: {n}" for tabela, n in counts.items())
        self.stdout.write(self.style.SUCCESS(f"Base sintética gerada em {path} ({resumo})."))
