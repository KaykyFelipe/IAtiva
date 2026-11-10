"""Consultas aprendidas (M9): consulta-modelo estruturada, criada por um administrador a partir
do dicionário e liberada só depois de aprovada. Guarda estrutura (tabela, campos, filtros), nunca
SQL nem dados."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class SituacaoModelo(models.TextChoices):
    PROPOSTA = "proposta", "Aguardando aprovação"
    APROVADA = "aprovada", "Aprovada"
    REJEITADA = "rejeitada", "Rejeitada"


class ConsultaModelo(models.Model):
    codigo = models.SlugField("código", max_length=60, unique=True)
    nome = models.CharField(max_length=120)
    descricao = models.CharField("descrição", max_length=300, blank=True)
    tabela = models.CharField(max_length=3, help_text="Alias do Protheus, ex.: SRA")
    # {"colunas": [{"campo", "rotulo", "tipo"}], "campo_data": str|"", "filtros_fixos": [{"campo", "valor"}],
    #  "ocultos": [campo], "ordenar": campo|""}
    spec = models.JSONField(default=dict)
    setores = models.ManyToManyField("iativa_accounts.Department", related_name="+",
                                     help_text="Setores que podem usar a consulta.")
    situacao = models.CharField(max_length=12, choices=SituacaoModelo.choices, default=SituacaoModelo.PROPOSTA)
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    aprovado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                     related_name="+")
    aprovado_em = models.DateTimeField(null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["nome"]
        verbose_name = "consulta aprendida"
        verbose_name_plural = "consultas aprendidas"

    def __str__(self):
        return f"{self.nome} ({self.tabela}, {self.get_situacao_display()})"

    def clean(self):
        from iativa.metadata.fontes import ALIAS_RE

        if not ALIAS_RE.match(self.tabela or ""):
            raise ValidationError({"tabela": "Alias inválido."})
        if not isinstance(self.spec, dict) or not self.spec.get("colunas"):
            raise ValidationError({"spec": "Informe as colunas."})
