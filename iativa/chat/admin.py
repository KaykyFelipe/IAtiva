from django.contrib import admin

from .models import AvaliacaoResposta, ConversationContext, RequestContext


class SomenteLeitura(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ConversationContext)
class ConversationContextAdmin(SomenteLeitura):
    list_display = ("id", "usuario", "setor", "criado_em", "encerrada_em")


@admin.register(RequestContext)
class RequestContextAdmin(SomenteLeitura):
    list_display = ("id", "usuario", "setor", "dominio", "objetivo", "estado", "compartilhado", "criado_em")
    list_filter = ("estado", "dominio", "setor")


@admin.register(AvaliacaoResposta)
class AvaliacaoRespostaAdmin(admin.ModelAdmin):
    """M134: respostas marcadas como incorretas, para a TI verificar o motivo e marcar como verificada."""
    list_display = ("id", "criado_em", "usuario", "correta", "verificada", "pergunta", "comentario")
    list_filter = ("correta", "verificada")
    list_editable = ("verificada",)
    readonly_fields = ("mensagem", "usuario", "correta", "comentario", "criado_em", "pergunta")
    fields = ("pergunta", "mensagem", "usuario", "correta", "comentario", "verificada", "criado_em")

    def has_add_permission(self, request):
        return False

    @admin.display(description="Pergunta")
    def pergunta(self, obj):
        p = obj.mensagem.conversa.mensagens.filter(papel="usuario", pk__lt=obj.mensagem_id).order_by("-pk").first()
        return (p.texto[:160] if p else "")
