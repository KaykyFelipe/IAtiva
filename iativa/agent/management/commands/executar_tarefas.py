"""Worker das tarefas longas do agente (M25). Roda em processo próprio (serviço `worker` no compose).

M73: vários agentes ao mesmo tempo (IATIVA_WORKER_AGENTES, padrão 6), cada um numa thread. Cada pedido é
reivindicado por lease atômico no banco, e um chat com pedido rodando não é pego por outro agente: chats
diferentes (de pessoas diferentes ou da mesma pessoa) andam em paralelo; o mesmo chat, um pedido por vez.

Uso: python manage.py executar_tarefas            (fica rodando)
     python manage.py executar_tarefas --uma-vez  (processa o que houver e sai)
"""
import signal
import threading
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections, connection


class Command(BaseCommand):
    help = "Executa as tarefas longas do agente fora do processo web."

    def add_arguments(self, parser):
        parser.add_argument("--uma-vez", action="store_true")
        parser.add_argument("--intervalo", type=float, default=2.0)
        parser.add_argument("--agentes", type=int, default=0)

    def _laco(self, parar, opts):
        from iativa.agent import tarefas

        try:
            while not parar.is_set():
                close_old_connections()
                try:
                    fez = tarefas.processar_proxima()
                except Exception as exc:  # o worker não morre por uma tarefa; detalhes podem conter dados
                    self.stderr.write(f"falha ao processar tarefa: {type(exc).__name__}")
                    fez = False
                if opts["uma_vez"] and not fez:
                    break
                if not fez:
                    parar.wait(opts["intervalo"])
        finally:
            connection.close()

    def handle(self, *args, **opts):
        parar = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: parar.set())
        n = 1 if opts["uma_vez"] else max(1, opts["agentes"] or int(getattr(settings, "IATIVA_WORKER_AGENTES", 1)))
        self.stdout.write(f"worker de tarefas iniciado ({n} agente(s) em paralelo)")
        if n == 1:
            self._laco(parar, opts)
            return
        threads = [threading.Thread(target=self._laco, args=(parar, opts), name=f"agente-{i}", daemon=True)
                   for i in range(n)]
        for i, t in enumerate(threads):
            t.start()
            time.sleep(0.2 * (i > 0))  # não disputam o mesmo item no primeiro instante
        while any(t.is_alive() for t in threads):
            for t in threads:
                t.join(timeout=1)
