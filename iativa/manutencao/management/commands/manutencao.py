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
        parser.add_argument("acao", choices=["listar", "exportar", "iniciar", "concluir", "retomar", "etapa", "cancelado", "ensaiar", "tentativa", "aguardar"])
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
                                                        condicoes={**{k: v for k, v in (i.condicoes or {}).items()
                                                                      if k not in ("tentativa_atual", "tentativa_motivo")},
                                                                   "etapa_inicio": timezone.now().timestamp()})
            self.stdout.write("ok")
            return
        if acao == "tentativa":
            # M122: o chat mostra em que tentativa está e por que a anterior não serviu (texto já sanitizado
            # pelo executor: partes do pedido que faltaram ou falha de teste; sem dados do ERP).
            from iativa.manutencao.incidentes import mascarar_pergunta
            i = Incidente.objects.filter(pk=o["id"]).first()
            n = int(o["situacao"] or 1)
            if i is not None:
                i.condicoes = {**(i.condicoes or {}), "tentativa_atual": n,
                               "tentativa_motivo": mascarar_pergunta(o["motivo"], 300)}
                i.save(update_fields=["condicoes"])
            self.stdout.write("ok")
            return
        if acao == "aguardar":
            # M125: o executor (não a correção) falhou: volta à fila sem gastar tentativa; elegivel() espera o
            # intervalo de recuperação antes de tentar de novo. Muitas falhas seguidas: vai para a TI.
            from iativa.manutencao.incidentes import EXECUTOR_MAX_FALHAS, mascarar_pergunta
            with transaction.atomic():
                i = Incidente.objects.select_for_update().get(pk=o["id"])
                n = int((i.condicoes or {}).get("executor_falhas", 0)) + 1
                motivo = mascarar_pergunta(o["motivo"], 300)
                cond = {**{k: v for k, v in (i.condicoes or {}).items() if k not in ("tentativa_atual", "tentativa_motivo")},
                        "executor_falha": motivo, "executor_falha_em": timezone.now().timestamp(), "executor_falhas": n}
                if n >= EXECUTOR_MAX_FALHAS:
                    Incidente.objects.filter(pk=i.pk).update(
                        situacao=SituacaoIncidente.BLOQUEADO, etapa="", condicoes=cond,
                        motivo=f"Ambiente de correção indisponível ({n} vezes seguidas): {motivo}"[:300])
                else:
                    Incidente.objects.filter(pk=i.pk).update(
                        situacao=SituacaoIncidente.ABERTO, etapa="", condicoes=cond,
                        tentativas=max(0, i.tentativas - 1))
            if n >= EXECUTOR_MAX_FALHAS:
                from iativa.agent import objetivos
                objetivos.encerrar_por_incidente(o["id"], f"Ambiente de correção indisponível: {motivo}")
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
    """Ensaia na sessão original em uma transação descartável, com a configuração e acesso do autor."""
    import os

    from iativa.agent.models import Objetivo, SituacaoObjetivo
    from iativa.chat import conversas
    from iativa.chat.models import Mensagem

    o = (Objetivo.objects.select_related("mensagem", "usuario", "conversa").filter(incidente_id=incidente_id)
         .order_by("-pk").first())
    if o is None:
        return "sem_pedido"
    if o.mensagem_id is None or not o.usuario.is_active or not conversas.pode_escrever(o.usuario, o.conversa):
        return "infra: contexto ou acesso original indisponível para o ensaio"
    anterior = os.environ.get("IATIVA_ENSAIO")
    os.environ["IATIVA_ENSAIO"] = "1"
    try:
        with transaction.atomic():
            if o.conversa.tipo != "individual":
                return "infra: ensaio isolado ainda indisponível para conversa compartilhada"
            from iativa.chat.models import ConversationContext
            c = ConversationContext.objects.create(tipo="individual", usuario=o.usuario, setor=o.conversa.setor)
            historico = list(o.conversa.mensagens.filter(pk__lt=o.mensagem_id).order_by("-pk")[:12])
            Mensagem.objects.bulk_create([
                Mensagem(conversa=c, papel=m.papel, texto=m.texto, autor_id=m.autor_id) for m in reversed(historico)
            ])
            resultado = _ensaiar_na_conversa(o, c)
            transaction.set_rollback(True)
            return resultado
    finally:
        if anterior is None:
            os.environ.pop("IATIVA_ENSAIO", None)
        else:
            os.environ["IATIVA_ENSAIO"] = anterior


def _ensaiar_na_conversa(o, c) -> str:
    from iativa.agent.models import Objetivo, SituacaoObjetivo
    from iativa.chat import conversas

    ator = o.usuario
    msg, res = conversas.enviar(ator, c, o.mensagem.texto)
    obj = Objetivo.objects.filter(conversa=c).order_by("-pk").first()
    ficha = getattr(msg, "ficha", None) or {}
    if ficha.get("tipo") in ("falha_modelo", "bloqueada", "esclarecimento_setor"):
        return "infra: atendimento indisponível no ensaio ou acesso inconclusivo"
    # M122: no ensaio não há worker: as continuações em segundo plano rodam aqui mesmo, como rodariam para a
    # pessoa. Antes, "sigo em segundo plano" contava como falha e a correção voltava a ser implementada.
    from uuid import uuid4

    from iativa.agent import agente
    continuou = False
    for _ in range(4):
        if obj is None or obj.situacao != SituacaoObjetivo.CONTINUAR:
            break
        Objetivo.objects.filter(pk=obj.pk).update(execucao=uuid4().hex, lease_ate=None)
        obj.refresh_from_db()
        agente.continuar_objetivo(obj)
        continuou = True
        obj.refresh_from_db()
    if continuou:
        ultima = c.mensagens.filter(papel="ia").order_by("-pk").first()
        if obj.situacao == SituacaoObjetivo.CUMPRIDO:
            return "ok"
        if (getattr(ultima, "ficha", None) or {}).get("tipo") == "falha_modelo":
            return "infra: atendimento indisponível durante a continuação do ensaio"
        return "falha: " + _motivo_ensaio(obj, getattr(ultima, "texto", ""))
    if ficha.get("corrigindo") or ficha.get("tipo") == "tarefa":
        return "falha: " + _motivo_ensaio(obj, msg.texto)
    if obj is not None and obj.situacao != SituacaoObjetivo.CUMPRIDO:
        return "falha: " + _motivo_ensaio(obj, msg.texto)
    if res is None or not (getattr(res, "ok", False) or getattr(res, "consolidado", False)):
        return "falha: " + " ".join(msg.texto.split())[:300]
    return "ok"


def _motivo_ensaio(obj, texto: str) -> str:
    """M122: o que o agente de manutenção precisa saber: as partes do pedido que não saíram (do contrato), e não
    um código interno como "continua: passos"."""
    contrato = (getattr(obj, "contrato", None) or {}) if obj is not None else {}
    entregas, feitas = contrato.get("entregas") or [], set(contrato.get("cumpridas") or [])
    faltam = [e.get("descricao", "") for i, e in enumerate(entregas) if i not in feitas and isinstance(e, dict)]
    if faltam:
        return ("não entregou: " + "; ".join(faltam) + (f" (entregou {len(feitas)} de {len(entregas)})"
                                                        if feitas else ""))[:300]
    return " ".join(((getattr(obj, "motivo", "") if obj is not None else "") or texto).split())[:300]
