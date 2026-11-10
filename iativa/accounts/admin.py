from django.contrib import admin

from .models import AccessPolicy, Department, UserProfile


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("nome", "codigo", "ativo")
    list_filter = ("ativo",)
    search_fields = ("nome", "codigo")


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "department", "funcao")
    list_filter = ("department", "funcao")
    search_fields = ("user__username",)


@admin.register(AccessPolicy)
class AccessPolicyAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "department",
        "pode_consultar",
        "pode_ler_memoria",
        "pode_alterar_operacao",
        "pode_aprovar_conhecimento",
        "pode_administrar",
        "valido_ate",
        "ativo",
    )
    list_filter = ("department", "ativo")
    search_fields = ("user__username",)
    readonly_fields = ("concedido_por", "criado_em")

    def save_model(self, request, obj, form, change):
        if not change:
            obj.concedido_por = request.user
        super().save_model(request, obj, form, change)
