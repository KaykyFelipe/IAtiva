"""Worker das tarefas longas do agente (M25). Roda em processo próprio (serviço `worker` no compose).

Uso: python manage.py executar_tarefas            (fica rodando)
     python manage.py executar_tarefas --uma-vez  (processa o que houver e sai)
"""
import signal
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections


class Command(BaseCommand):
    help = "Executa as tarefas longas do agente fora do processo web."

    def add_arguments(self, parser):
        parser.add_argument("--uma-vez", action="store_true")
        parser.add_argument("--intervalo", type=float, default=2.0)

    def handle(self, *args, **opts):
        from iativa.agent import tarefas

        parar = {"sim": False}
        signal.signal(signal.SIGTERM, lambda *_: parar.update(sim=True))
        self.stdout.write("worker de tarefas iniciado")
        while not parar["sim"]:
            close_old_connections()
            try:
                fez = tarefas.processar_proxima()
            except Exception as exc:  # o worker não morre por uma tarefa; detalhes podem conter dados
                self.stderr.write(f"falha ao processar tarefa: {type(exc).__name__}")
                fez = False
            if opts["uma_vez"] and not fez:
                break
            if not fez:
                time.sleep(opts["intervalo"])
