from django.contrib import admin

from .models import OperationAudit


@admin.register(OperationAudit)
class OperationAuditAdmin(admin.ModelAdmin):
    """Somente leitura: auditoria não é criada, alterada nem apagada pelo admin."""

    list_display = ("criado_em", "usuario", "setor", "acao", "entidade", "entidade_id", "estado_novo", "resultado")
    list_filter = ("acao", "entidade", "resultado")
    date_hierarchy = "criado_em"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
