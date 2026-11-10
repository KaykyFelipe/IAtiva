from django.contrib import admin

from .models import ConsultaModelo


@admin.register(ConsultaModelo)
class ConsultaModeloAdmin(admin.ModelAdmin):
    """Somente leitura: propor, aprovar e rejeitar acontecem pelo chat, com as regras de M9."""

    list_display = ("nome", "tabela", "situacao", "criado_por", "aprovado_por", "criado_em")
    list_filter = ("situacao", "tabela")
    readonly_fields = ("codigo", "nome", "descricao", "tabela", "spec", "situacao", "criado_por",
                       "aprovado_por", "aprovado_em", "criado_em")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
