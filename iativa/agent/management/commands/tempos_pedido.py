"""M125: tempo de cada pedido, do envio até a resposta aparecer no chat, separado por etapa. Sem texto nem dados.

  manage.py tempos_pedido                 últimos 20 pedidos
  manage.py tempos_pedido --mensagem N    um pedido
  manage.py tempos_pedido --usuario login --limite 5
"""
from django.core.management.base import BaseCommand

META_S = 300


class Command(BaseCommand):
    help = "Tempos por etapa dos pedidos (meta: resposta conferida em menos de 5 minutos)."

    def add_arguments(self, parser):
        parser.add_argument("--mensagem", type=int)
        parser.add_argument("--usuario", default="")
        parser.add_argument("--limite", type=int, default=20)

    def handle(self, *args, mensagem=None, usuario="", limite=20, **o):
        from iativa.agent.models import Objetivo
        from iativa.chat.models import Mensagem, Papel

        qs = Mensagem.objects.filter(papel=Papel.USUARIO).select_related("conversa").order_by("-pk")
        if mensagem:
            qs = qs.filter(pk=mensagem)
        if usuario:
            qs = qs.filter(autor__username=usuario)
        for m in qs[:limite]:
            prox = Mensagem.objects.filter(conversa=m.conversa, papel=Papel.USUARIO, pk__gt=m.pk).order_by("pk").first()
            respostas = Mensagem.objects.filter(conversa=m.conversa, papel=Papel.IA, pk__gt=m.pk)
            if prox is not None:
                respostas = respostas.filter(pk__lt=prox.pk)
            linhas = []
            final = None
            for r in respostas.order_by("pk"):
                f = r.ficha or {}
                pendente = f.get("tipo") == "objetivo" or bool(f.get("corrigindo"))
                if not pendente:
                    final = r
                t = f.get("tempos_ms") or {}
                linhas.append(f"    +{(r.criado_em - m.criado_em).total_seconds():7.1f}s "
                              f"{'parcial ' if pendente else 'resposta'} {f.get('tipo', '') or '-'} "
                              + " ".join(f"{k}={v / 1000:.1f}s" for k, v in t.items()))
            obj = Objetivo.objects.filter(mensagem=m).select_related("incidente").order_by("-pk").first()
            inc = obj.incidente if obj is not None else None
            total = (final.criado_em - m.criado_em).total_seconds() if final is not None else None
            situacao = ("sem resposta final" if total is None
                        else f"{total:.1f}s {'OK' if total < META_S else 'ACIMA DA META'}")
            self.stdout.write(f"mensagem {m.pk} ({m.criado_em:%d/%m %H:%M:%S}): {situacao}"
                              + (f" | objetivo {obj.pk} {obj.situacao} passos={obj.passos} continuações={obj.continuacoes}"
                                 if obj is not None else "")
                              + (f" | incidente #{inc.pk} {inc.situacao} tentativas={inc.tentativas}" if inc else ""))
            for ln in linhas:
                self.stdout.write(ln)
