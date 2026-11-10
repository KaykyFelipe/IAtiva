from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError

from . import servicos
from .models import MemoryRevision, ParameterBinding, ParameterDefinition


@admin.register(ParameterDefinition)
class ParameterDefinitionAdmin(admin.ModelAdmin):
    list_display = ("codigo", "nome", "tipo", "contem_dado_pessoal", "ativo")
    list_filter = ("tipo", "contem_dado_pessoal", "ativo")
    search_fields = ("codigo", "nome")
    readonly_fields = ("criado_por", "criado_em", "atualizado_em")

    def save_model(self, request, obj, form, change):
        if not change:
            obj.criado_por = request.user
        super().save_model(request, obj, form, change)


class RevisaoInline(admin.TabularInline):
    model = MemoryRevision
    extra = 0
    can_delete = False
    fields = ("criado_em", "acao", "situacao_anterior", "situacao_nova", "autor", "motivo")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(ParameterBinding)
class ParameterBindingAdmin(admin.ModelAdmin):
    """Valores são criados e alterados só pelos serviços, que aplicam permissões e histórico."""

    list_display = ("definicao", "escopo", "setor", "usuario", "situacao", "obrigatoria", "versao", "valido_ate")
    list_filter = ("situacao", "escopo", "setor", "obrigatoria")
    inlines = [RevisaoInline]
    actions = ["aprovar_propostas", "rejeitar_propostas"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def _aplicar(self, request, queryset, funcao, verbo):
        ok = falhas = 0
        for binding in queryset:
            try:
                funcao(request.user, binding)
                ok += 1
            except (PermissionDenied, ValidationError):
                falhas += 1
        if ok:
            self.message_user(request, f"{ok} proposta(s) {verbo}(s).", messages.SUCCESS)
        if falhas:
            self.message_user(request, f"{falhas} item(ns) recusado(s) por permissão ou situação.", messages.WARNING)

    @admin.action(description="Aprovar propostas selecionadas")
    def aprovar_propostas(self, request, queryset):
        self._aplicar(request, queryset, servicos.aprovar, "aprovada")

    @admin.action(description="Rejeitar propostas selecionadas")
    def rejeitar_propostas(self, request, queryset):
        self._aplicar(request, queryset, servicos.rejeitar, "rejeitada")
