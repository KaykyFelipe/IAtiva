"""M149: lista os erros da IA registrados (pergunta, resposta, o que corrigir) e marca como corrigidos.

    manage.py erros_ia                 # últimos 20 ainda não corrigidos
    manage.py erros_ia --todos -n 50   # inclui os corrigidos
    manage.py erros_ia --corrigido 12 13
"""
from django.core.management.base import BaseCommand

from iativa.chat.models import ErroResposta


class Command(BaseCommand):
    help = "Lista os erros da IA (pergunta, resposta e o que corrigir) ou marca como corrigidos."

    def add_arguments(self, parser):
        parser.add_argument("-n", type=int, default=20)
        parser.add_argument("--todos", action="store_true")
        parser.add_argument("--corrigido", type=int, nargs="+", metavar="ID")

    def handle(self, *args, n, todos, corrigido, **opts):
        if corrigido:
            total = ErroResposta.objects.filter(pk__in=corrigido).update(corrigido=True)
            self.stdout.write(f"{total} marcado(s) como corrigido(s).")
            return
        qs = ErroResposta.objects.all() if todos else ErroResposta.objects.filter(corrigido=False)
        for e in qs.order_by("-pk")[:n]:
            self.stdout.write(f"#{e.pk} {e.criado_em:%d/%m %H:%M} [{e.tipo}]{' (corrigido)' if e.corrigido else ''} "
                              f"versão {e.versao}")
            self.stdout.write(f"  Pergunta: {e.pergunta}")
            self.stdout.write(f"  Resposta: {e.resposta}")
            self.stdout.write(f"  Corrigir: {e.erro}\n")
