"""Auditoria sanitizada (PLANO.md §4).

Registra quem, quando, entidade, estado, duração e número de linhas. Não há campo de texto
livre: ação, entidade e estados são códigos curtos validados, portanto SQL, valores de
parâmetros, mensagens e resultados não têm onde ser gravados.
Registros são imutáveis; a remoção só acontece pelo expurgo de retenção.
"""
import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

CODIGO_RE = re.compile(r"^[a-z0-9_]{0,40}$")


class Acao(models.TextChoices):
    SOLICITACAO_CRIADA = "solicitacao_criada", "Solicitação criada"
    TRANSICAO = "transicao_estado", "Transição de estado"
    PARAMETRO_REGISTRADO = "parametro_registrado", "Parâmetro registrado"
    PARAMETRO_APROVADO = "parametro_aprovado", "Parâmetro aprovado"
    PARAMETRO_REJEITADO = "parametro_rejeitado", "Parâmetro rejeitado"
    PARAMETRO_CORRIGIDO = "parametro_corrigido", "Parâmetro corrigido"
    PARAMETRO_REVOGADO = "parametro_revogado", "Parâmetro revogado"
    ACESSO_NEGADO = "acesso_negado", "Acesso negado"
    EXPURGO = "expurgo", "Expurgo por retenção"
    METADADO_CONSULTADO = "metadado_consultado", "Metadado consultado"
    CONHECIMENTO_CONSULTADO = "conhecimento_consultado", "Conhecimento consultado"
    INTERPRETACAO = "interpretacao", "Mensagem interpretada"
    MODELO_FALHOU = "modelo_falhou", "Falha do modelo de IA"
    CONSULTA_EXECUTADA = "consulta_executada", "Consulta executada"


class Entidade(models.TextChoices):
    SOLICITACAO = "solicitacao", "Solicitação"
    PARAMETRO = "parametro", "Valor de parâmetro"
    PERMISSAO = "permissao", "Permissão"
    RETENCAO = "retencao", "Retenção"
    METADADO = "metadado", "Metadado"
    CONHECIMENTO = "conhecimento", "Conhecimento"
    AGENTE = "agente", "Agente"


class Resultado(models.TextChoices):
    OK = "ok", "OK"
    NEGADO = "negado", "Negado"
    ERRO = "erro", "Erro"


class OperationAudit(models.Model):
    criado_em = models.DateTimeField(default=timezone.now, db_index=True)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    setor = models.ForeignKey(
        "iativa_accounts.Department", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    acao = models.CharField("ação", max_length=40, choices=Acao.choices)
    entidade = models.CharField(max_length=30, choices=Entidade.choices)
    entidade_id = models.PositiveBigIntegerField(null=True, blank=True)
    estado_anterior = models.CharField(max_length=40, blank=True)
    estado_novo = models.CharField(max_length=40, blank=True)
    resultado = models.CharField(max_length=10, choices=Resultado.choices, default=Resultado.OK)
    duracao_ms = models.PositiveIntegerField(null=True, blank=True)
    linhas = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["-criado_em"]
        verbose_name = "registro de auditoria"
        verbose_name_plural = "auditoria"

    def __str__(self):
        return f"{self.criado_em:%Y-%m-%d %H:%M:%S} {self.acao} {self.entidade}#{self.entidade_id or '-'}"

    def clean(self):
        errors = {}
        if self.acao not in Acao.values:
            errors["acao"] = "Ação desconhecida."
        if self.entidade not in Entidade.values:
            errors["entidade"] = "Entidade desconhecida."
        if self.resultado not in Resultado.values:
            errors["resultado"] = "Resultado desconhecido."
        for campo in ("estado_anterior", "estado_novo"):
            if not CODIGO_RE.match(getattr(self, campo) or ""):
                errors[campo] = "Somente códigos curtos (a-z, 0-9, _)."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Registros de auditoria são imutáveis.")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Registros de auditoria só são removidos pelo expurgo de retenção.")
