"""Contextos de conversa e de solicitação (Etapa 2).

Guardam somente dados estruturados: dono, setor, domínio, código do objetivo e estado.
Não existe campo para o texto das mensagens, SQL ou resultados (PEDIDO_INICIAL §4 e §14).
"""
from django.conf import settings
from django.db import models


class Dominio(models.TextChoices):
    FINANCEIRO_RECEBER = "financeiro_receber", "Financeiro — Contas a Receber"


class EstadoSolicitacao(models.TextChoices):
    RASCUNHO = "rascunho", "Rascunho"
    AGUARDANDO_PARAMETROS = "aguardando_parametros", "Aguardando parâmetros"
    VALIDANDO = "validando", "Validando"
    PRONTA = "pronta", "Pronta"
    EXECUTANDO = "executando", "Executando"
    CONCLUIDA = "concluida", "Concluída"
    NAO_VALIDADA = "nao_validada", "Não validada"
    FALHOU = "falhou", "Falhou"
    BLOQUEADA = "bloqueada", "Bloqueada"
    CANCELADA = "cancelada", "Cancelada"


class ConversationContext(models.Model):
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="conversas_iativa"
    )
    setor = models.ForeignKey("iativa_accounts.Department", on_delete=models.PROTECT, related_name="+")
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)
    encerrada_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "conversa"
        verbose_name_plural = "conversas"

    def __str__(self):
        return f"Conversa #{self.pk} — {self.usuario}"


class RequestContext(models.Model):
    conversa = models.ForeignKey(ConversationContext, on_delete=models.CASCADE, related_name="solicitacoes")
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="solicitacoes_iativa"
    )
    setor = models.ForeignKey("iativa_accounts.Department", on_delete=models.PROTECT, related_name="+")
    dominio = models.CharField("domínio", max_length=40, choices=Dominio.choices)
    objetivo = models.SlugField(
        max_length=64, help_text="Código estruturado do objetivo (ex.: titulos_em_atraso). Nunca o texto da pergunta."
    )
    estado = models.CharField(
        max_length=30, choices=EstadoSolicitacao.choices, default=EstadoSolicitacao.RASCUNHO
    )
    compartilhado = models.BooleanField(
        default=False, help_text="Compartilhada com quem pode ler a memória do setor."
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)
    encerrado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "solicitação"
        verbose_name_plural = "solicitações"
        indexes = [models.Index(fields=["estado", "encerrado_em"])]

    def __str__(self):
        return f"Solicitação #{self.pk} ({self.objetivo}, {self.estado})"
