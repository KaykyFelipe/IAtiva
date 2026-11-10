from django.contrib import admin

from .models import DominioSetor, RestricaoMetadado


@admin.register(DominioSetor)
class DominioSetorAdmin(admin.ModelAdmin):
    list_display = ("dominio", "setor", "ativo")
    list_filter = ("dominio", "ativo")


@admin.register(RestricaoMetadado)
class RestricaoMetadadoAdmin(admin.ModelAdmin):
    list_display = ("tabela", "campo", "ativo")
    list_filter = ("tabela", "ativo")
    filter_horizontal = ("setores_permitidos",)
