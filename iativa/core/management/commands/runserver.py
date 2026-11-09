"""Servidor de desenvolvimento restrito a 127.0.0.1.

Substitui o `runserver` padrão: recusa qualquer endereço que não seja 127.0.0.1/localhost
(incluindo IPs da rede e IPv6). Serve arquivos estáticos localmente mesmo com DEBUG=False.
Não é servidor de produção.

Exceção controlada (decisão T6): dentro de um container Docker, o processo precisa escutar em
0.0.0.0 para que a porta publicada funcione. Isso só é aceito quando AMBAS as condições valem:
o processo está num container (arquivo /.dockerenv) e IATIVA_ALLOW_CONTAINER_BIND=1. Nesse caso,
a restrição a localhost é garantida pela publicação da porta em 127.0.0.1 do host (compose.yaml).
"""
import os
from pathlib import Path

from django.contrib.staticfiles.management.commands.runserver import Command as StaticRunserverCommand
from django.core.management.base import CommandError

PERMITTED_ADDRS = {"127.0.0.1", "localhost"}
CONTAINER_BIND_ADDR = "0.0.0.0"
CONTAINER_MARKER = Path("/.dockerenv")
CONTAINER_BIND_ENV = "IATIVA_ALLOW_CONTAINER_BIND"


def container_bind_allowed() -> bool:
    return os.environ.get(CONTAINER_BIND_ENV) == "1" and CONTAINER_MARKER.exists()


class Command(StaticRunserverCommand):
    help = "Inicia o servidor de desenvolvimento SOMENTE em 127.0.0.1 (Etapa 1 — localhost)."
    default_addr = "127.0.0.1"

    def handle(self, *args, **options):
        if options.get("use_ipv6"):
            raise CommandError("IPv6 desativado nesta fase: use 127.0.0.1.")
        # DEBUG é fixo em False; os estáticos são servidos apenas por este servidor local.
        options["insecure_serving"] = True
        super().handle(*args, **options)

    def run(self, **options):
        if self.addr in PERMITTED_ADDRS:
            return super().run(**options)
        if self.addr == CONTAINER_BIND_ADDR and container_bind_allowed():
            self.stdout.write(
                "Escutando em 0.0.0.0 DENTRO do container; a porta deve ser publicada só em "
                "127.0.0.1 do host (ver compose.yaml)."
            )
            return super().run(**options)
        raise CommandError(
            f"Endereço '{self.addr}' recusado. Nesta fase o servidor só aceita 127.0.0.1 "
            "(acesso externo é a Etapa 9 e exige autorização)."
        )
