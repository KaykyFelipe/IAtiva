from django.contrib import admin

from .models import Incidente


@admin.register(Incidente)
class IncidenteAdmin(admin.ModelAdmin):
    list_display = ("id", "codigo", "categoria", "situacao", "ocorrencias", "tentativas", "versao", "versao_corrigida")
    list_filter = ("categoria", "situacao")
    readonly_fields = [f.name for f in Incidente._meta.fields]

    def has_add_permission(self, request):
        return False
