"""Interface do executor de manutenção com a aplicação (M32). Só dados sanitizados entram e saem.

  manage.py manutencao listar                  incidentes (texto)
  manage.py manutencao exportar                elegíveis para correção automática (JSON)
  manage.py manutencao iniciar <id>            marca em correção (+1 tentativa); falha se não elegível
  manage.py manutencao concluir <id> <situacao> [--commit X] [--versao Y] [--motivo Z]
  manage.py manutencao etapa <id> <codigo>     M53: etapa em andamento, mostrada no chat (models.ETAPAS)
  manage.py manutencao retomar <id>            retoma UMA vez as tarefas que falharam por esse incidente
  manage.py manutencao ensaiar <id>            M120: refaz o pedido real do incidente numa conversa temporária
                                               (imagem candidata, IATIVA_ENSAIO=1) e diz se entregou: "ok",
                                               "sem_pedido" ou "falha: <motivo>"
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
        parser.add_argument("acao", choices=["listar", "exportar", "iniciar", "concluir", "retomar", "etapa", "cancelado", "ensaiar"])
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
        from django.utils import timezone

        if acao == "etapa":
            from iativa.manutencao.models import ETAPAS

            if o["situacao"] not in ETAPAS:
                raise CommandError("etapa inválida")
            with transaction.atomic():
                i = Incidente.objects.select_for_update().get(pk=o["id"])
                if i.situacao == SituacaoIncidente.EM_CORRECAO:
                    Incidente.objects.filter(pk=i.pk).update(
                        etapa=o["situacao"], condicoes={**(i.condicoes or {}), "etapa_inicio": timezone.now().timestamp()})
            self.stdout.write("ok")
            return
        if acao == "iniciar":
            with transaction.atomic():
                i = Incidente.objects.select_for_update().get(pk=o["id"])
                if not elegivel(i):
                    raise CommandError("incidente não elegível")
                Incidente.objects.filter(pk=i.pk).update(situacao=SituacaoIncidente.EM_CORRECAO, etapa="analisando",
                                                        tentativas=F("tentativas") + 1,
                                                        condicoes={**(i.condicoes or {}), "etapa_inicio": timezone.now().timestamp()})
            self.stdout.write("ok")
            return
        if acao == "ensaiar":
            self.stdout.write(ensaiar(o["id"]))
            return
        if acao == "cancelado":
            # M114: o executor pergunta, entre as etapas, se a pessoa cancelou (botão parar).
            i = Incidente.objects.filter(pk=o["id"]).first()
            self.stdout.write("sim" if i is None or (i.situacao == SituacaoIncidente.BLOQUEADO
                                                     and i.motivo.startswith("Cancelada pela pessoa")) else "nao")
            return
        if acao == "concluir":
            if o["situacao"] not in SituacaoIncidente.values:
                raise CommandError("situação inválida")
            atual = Incidente.objects.filter(pk=o["id"]).values_list("situacao", "motivo").first()
            if atual and atual[0] == SituacaoIncidente.BLOQUEADO and atual[1].startswith("Cancelada pela pessoa"):
                Incidente.objects.filter(pk=o["id"]).update(commit=o["commit"][:40])  # M114: cancelamento prevalece
                self.stdout.write("cancelado")
                return
            Incidente.objects.filter(pk=o["id"]).update(situacao=o["situacao"], commit=o["commit"][:40],
                                                         versao_corrigida=o["versao"][:40], motivo=o["motivo"][:300])
            if o["situacao"] in (SituacaoIncidente.FALHOU, SituacaoIncidente.BLOQUEADO, SituacaoIncidente.NAO_ELEGIVEL):
                # Sem correção automática: os pedidos que esperavam por ela não ficam pendurados em silêncio.
                from iativa.agent import objetivos

                objetivos.encerrar_por_incidente(o["id"], o["motivo"])
            self.stdout.write("ok")
            return
        if acao == "retomar":
            from iativa.agent.tarefas import retomar_apos_correcao

            self.stdout.write(f"retomadas={retomar_apos_correcao(o['id'])}")


def ensaiar(incidente_id: int) -> str:
    """M120: antes de publicar, a imagem candidata responde ao pedido que abriu o incidente, como a própria
    pessoa, numa conversa temporária (apagada no fim). Passa pelo mesmo ciclo, revisor incluído. Só "ok" publica."""
    from iativa.agent.models import Objetivo, SituacaoObjetivo
    from iativa.chat import conversas

    o = (Objetivo.objects.select_related("mensagem", "usuario").filter(incidente_id=incidente_id)
         .order_by("-pk").first())
    if o is None or o.mensagem_id is None or not o.usuario.is_active:
        return "sem_pedido"
    c = conversas.nova_individual(o.usuario)
    try:
        msg, res = conversas.enviar(o.usuario, c, o.mensagem.texto)
        obj = c.objetivos.order_by("-pk").first() if hasattr(c, "objetivos") else None
        ficha = getattr(msg, "ficha", None) or {}
        if ficha.get("corrigindo") or ficha.get("tipo") == "tarefa":
            return "falha: " + " ".join((getattr(obj, "motivo", "") or msg.texto).split())[:300]
        if obj is not None and obj.situacao != SituacaoObjetivo.CUMPRIDO:
            return "falha: " + " ".join((obj.motivo or msg.texto).split())[:300]
        if res is None or not (getattr(res, "ok", False) or getattr(res, "consolidado", False)):
            return "falha: " + " ".join(msg.texto.split())[:300]
        return "ok"
    finally:
        c.delete()
