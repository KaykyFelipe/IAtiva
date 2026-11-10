"""Restrições de acesso à descoberta de metadados (PEDIDO_INICIAL §10: "também na descoberta")."""
from django.core.exceptions import ValidationError
from django.db import models

from .dominios import DOMINIOS
from .fontes import ALIAS_RE, CAMPO_RE

DOMINIO_CHOICES = [(k, v["nome"]) for k, v in DOMINIOS.items()]


class DominioSetor(models.Model):
    """Setor habilitado a usar um domínio. Sem vínculo, ninguém do setor descobre o domínio."""

    dominio = models.CharField("domínio", max_length=40, choices=DOMINIO_CHOICES)
    setor = models.ForeignKey("iativa_accounts.Department", on_delete=models.CASCADE, related_name="+")
    ativo = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["dominio", "setor"], name="dominio_setor_unico")]
        verbose_name = "domínio por setor"
        verbose_name_plural = "domínios por setor"

    def __str__(self):
        return f"{self.dominio} — {self.setor}"


class RestricaoMetadado(models.Model):
    """Esconde uma tabela inteira (campo vazio) ou um campo, exceto para os setores listados."""

    tabela = models.CharField(max_length=3, help_text="Alias, ex.: SA1")
    campo = models.CharField(max_length=10, blank=True, help_text="Vazio = tabela inteira")
    setores_permitidos = models.ManyToManyField("iativa_accounts.Department", blank=True, related_name="+")
    ativo = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tabela", "campo"], name="restricao_metadado_unica")]
        verbose_name = "restrição de metadado"
        verbose_name_plural = "restrições de metadados"

    def __str__(self):
        return f"{self.tabela}.{self.campo or '*'}"

    def clean(self):
        self.tabela = (self.tabela or "").upper()
        self.campo = (self.campo or "").upper()
        if not ALIAS_RE.match(self.tabela):
            raise ValidationError({"tabela": "Alias inválido (ex.: SA1)."})
        if self.campo and not CAMPO_RE.match(self.campo):
            raise ValidationError({"campo": "Nome de campo inválido."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
