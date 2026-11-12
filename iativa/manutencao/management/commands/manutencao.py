"""Interface do executor de manutenção com a aplicação (M32). Só dados sanitizados entram e saem.

  manage.py manutencao listar                  incidentes (texto)
  manage.py manutencao exportar                elegíveis para correção automática (JSON)
  manage.py manutencao iniciar <id>            marca em correção (+1 tentativa); falha se não elegível
  manage.py manutencao concluir <id> <situacao> [--commit X] [--versao Y] [--motivo Z]
  manage.py manutencao retomar <id>            retoma UMA vez as tarefas que falharam por esse incidente
"""
import json

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import F

from iativa.manutencao.incidentes import diagnostico, elegivel
from iativa.manutencao.models import Incidente, SituacaoIncidente


class Command(BaseCommand):
    help = "Incidentes técnicos para o executor de manutenção (sem dados do ERP)."

    def add_arguments(self, parser):
        parser.add_argument("acao", choices=["listar", "exportar", "iniciar", "concluir", "retomar"])
        parser.add_argument("id", nargs="?", type=int)
        parser.add_argument("situacao", nargs="?")
        parser.add_argument("--commit", default="")
        parser.add_argument("--versao", default="")
        parser.add_argument("--motivo", default="")

    def handle(self, *args, acao, **o):
        if acao == "listar":
            for i in Incidente.objects.all()[:50]:
                self.stdout.write(f"#{i.pk} {i.get_categoria_display()} | {i.codigo} | {i.get_situacao_display()} | "
                                  f"ocorrências {i.ocorrencias} | tentativas {i.tentativas} | versão {i.versao}"
                                  + (f" | corrigido em {i.versao_corrigida} ({i.commit[:12]})" if i.commit else ""))
            return
        if acao == "exportar":
            self.stdout.write(json.dumps([diagnostico(i) for i in Incidente.objects.all() if elegivel(i)]))
            return
        if o["id"] is None:
            raise CommandError("informe o id do incidente")
        if acao == "iniciar":
            with transaction.atomic():
                i = Incidente.objects.select_for_update().get(pk=o["id"])
                if not elegivel(i):
                    raise CommandError("incidente não elegível")
                Incidente.objects.filter(pk=i.pk).update(situacao=SituacaoIncidente.EM_CORRECAO,
                                                        tentativas=F("tentativas") + 1)
            self.stdout.write("ok")
            return
        if acao == "concluir":
            if o["situacao"] not in SituacaoIncidente.values:
                raise CommandError("situação inválida")
            Incidente.objects.filter(pk=o["id"]).update(situacao=o["situacao"], commit=o["commit"][:40],
                                                         versao_corrigida=o["versao"][:40], motivo=o["motivo"][:300])
            self.stdout.write("ok")
            return
        if acao == "retomar":
            from iativa.agent.tarefas import retomar_apos_correcao

            self.stdout.write(f"retomadas={retomar_apos_correcao(o['id'])}")
