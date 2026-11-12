"""Confere se as migrações pendentes são compatíveis com a versão anterior ainda no ar (M31).

Enquanto as duas versões rodam juntas, o banco precisa servir às duas. Só passam mudanças
aditivas que o código antigo tolera:
- CreateModel, AddIndex;
- AddField com null=True ou com valor padrão no próprio banco (db_default);
- RunPython só se a migração declarar `IATIVA_COMPATIVEL = True` (ex.: inserir um parâmetro novo).
Qualquer outra coisa (remover, renomear ou alterar campo/tabela, SQL bruto, campo obrigatório sem
padrão no banco) bloqueia a publicação automática; ela pode ser feita à mão, em janela combinada.

Saída: 0 = compatível (ou nada pendente); 3 = incompatível. Só imprime nomes de migrações e operações.
"""
import sys

from django.core.management.base import BaseCommand
from django.db import connection, migrations
from django.db.migrations.executor import MigrationExecutor
from django.db.models import NOT_PROVIDED

ADITIVAS = (migrations.CreateModel, migrations.AddIndex)


def avaliar_operacao(migracao, op) -> str:
    """Motivo da incompatibilidade, ou vazio se a operação é aditiva e segura."""
    if isinstance(op, ADITIVAS):
        return ""
    if isinstance(op, migrations.AddField):
        f = op.field
        if f.null or getattr(f, "db_default", NOT_PROVIDED) is not NOT_PROVIDED:
            return ""
        return f"campo obrigatório sem padrão no banco ({op.model_name}.{op.name}): a versão anterior não o preenche"
    if isinstance(op, migrations.RunPython):
        return "" if getattr(migracao, "IATIVA_COMPATIVEL", False) else "RunPython sem IATIVA_COMPATIVEL = True"
    return f"{type(op).__name__} não é aditiva"


def pendentes():
    executor = MigrationExecutor(connection)
    plano = executor.migration_plan(executor.loader.graph.leaf_nodes())
    return [(m, recuar) for m, recuar in plano]


class Command(BaseCommand):
    help = "Diz se as migrações pendentes podem ser aplicadas com a versão anterior ainda no ar."

    def handle(self, *args, **opts):
        problemas = []
        lista = pendentes()
        for migracao, recuar in lista:
            nome = f"{migracao.app_label}.{migracao.name}"
            if recuar:
                problemas.append(f"{nome}: desfazer migração")
                continue
            modulo = sys.modules.get(type(migracao).__module__)
            for op in migracao.operations:
                motivo = avaliar_operacao(modulo or migracao, op)
                if motivo:
                    problemas.append(f"{nome}: {motivo}")
        self.stdout.write(f"migracoes_pendentes={len(lista)}")
        for p in problemas:
            self.stdout.write(f"INCOMPATIVEL {p}")
        if problemas:
            sys.exit(3)
        self.stdout.write("COMPATIVEL")
