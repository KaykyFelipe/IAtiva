from django.contrib import admin

from .models import ConversationContext, RequestContext


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
