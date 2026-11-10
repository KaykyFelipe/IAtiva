"""Setores, vínculo do usuário e políticas de acesso (Etapa 2).

Regras (docs/PEDIDO_INICIAL.md §10):
- Identidade, setor e filiais são resolvidos pelo backend a partir destes modelos.
- Permissões separadas: consulta, leitura de memória, alteração operacional, aprovação de
  conhecimento e administração da plataforma.
- Nenhuma permissão é universal ou automática: função (diretoria/TI) e superusuário do admin
  não concedem acesso a dados. Toda permissão vem de uma `AccessPolicy` explícita.
"""
import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

FILIAL_RE = re.compile(r"^[0-9A-Za-z]{1,12}$")


class Department(models.Model):
    codigo = models.SlugField("código", max_length=40, unique=True)
    nome = models.CharField(max_length=120)
    ativo = models.BooleanField(default=True)

    class Meta:
        ordering = ["nome"]
        verbose_name = "setor"
        verbose_name_plural = "setores"

    def __str__(self):
        return self.nome


class Funcao(models.TextChoices):
    COLABORADOR = "colaborador", "Colaborador"
    GESTOR = "gestor", "Gestor do setor"
    DIRETORIA = "diretoria", "Diretoria"
    TI = "ti", "TI"


class UserProfile(models.Model):
    """Setor e função do usuário. A função é informativa: não concede permissões."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="perfil_iativa")
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="membros", verbose_name="setor")
    funcao = models.CharField("função", max_length=20, choices=Funcao.choices, default=Funcao.COLABORADOR)

    class Meta:
        verbose_name = "perfil do usuário"
        verbose_name_plural = "perfis dos usuários"

    def __str__(self):
        return f"{self.user} — {self.department}"


class AccessPolicy(models.Model):
    """Concede a um usuário permissões sobre os dados de UM setor, limitadas a filiais.

    `department` vazio significa nível empresa e só vale para leitura de memória da empresa,
    aprovação de definições da empresa e administração. Consulta e alteração sempre exigem um
    setor e filiais explícitas: não existe política "todos os setores".
    A visão consolidada (diretoria/TI) é a soma de políticas explícitas, uma por setor.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="politicas_acesso", verbose_name="usuário"
    )
    department = models.ForeignKey(
        Department,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="politicas",
        verbose_name="setor",
        help_text="Vazio = nível empresa (somente memória, aprovação e administração).",
    )
    pode_consultar = models.BooleanField("consultar dados", default=False)
    pode_ler_memoria = models.BooleanField("ler memória", default=False)
    pode_alterar_operacao = models.BooleanField("alteração operacional", default=False)
    pode_aprovar_conhecimento = models.BooleanField("aprovar definições", default=False)
    pode_administrar = models.BooleanField("administrar a plataforma", default=False)
    filiais = models.JSONField(
        default=list, blank=True, help_text='Lista de códigos de filial, por exemplo ["01", "02"].'
    )
    valido_de = models.DateTimeField("válido de", default=timezone.now)
    valido_ate = models.DateTimeField("válido até", null=True, blank=True)
    ativo = models.BooleanField(default=True)
    concedido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "política de acesso"
        verbose_name_plural = "políticas de acesso"

    def __str__(self):
        alvo = self.department or "empresa"
        return f"{self.user} → {alvo}"

    def clean(self):
        errors = {}
        filiais = self.filiais
        if not isinstance(filiais, list) or not all(isinstance(f, str) and FILIAL_RE.match(f) for f in filiais):
            errors["filiais"] = "Use uma lista de códigos de filial (letras e números, até 12 caracteres)."
        exige_setor = self.pode_consultar or self.pode_alterar_operacao
        if exige_setor and self.department_id is None:
            errors["department"] = "Consulta e alteração exigem um setor: não existe permissão universal."
        if exige_setor and not errors.get("filiais") and not filiais:
            errors["filiais"] = "Informe as filiais permitidas para consulta e alteração."
        if self.valido_ate and self.valido_de and self.valido_ate <= self.valido_de:
            errors["valido_ate"] = "A validade final deve ser posterior à inicial."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
