"""Contextos de conversa e de solicitação (Etapa 2).

Guardam somente dados estruturados: dono, setor, domínio, código do objetivo e estado.
Não existe campo para o texto das mensagens, SQL ou resultados (PEDIDO_INICIAL §4 e §14).
"""
from django.conf import settings
from django.db import models


class Dominio(models.TextChoices):
    FINANCEIRO_RECEBER = "financeiro_receber", "Financeiro — Contas a Receber"
    APRENDIDO = "aprendido", "Consulta aprendida (M9)"


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


class TipoConversa(models.TextChoices):
    INDIVIDUAL = "individual", "Individual"
    SETOR = "setor", "Setor"
    MESTRE = "mestre", "Chat mestre"


class ConversationContext(models.Model):
    """Conversa. Individual e mestre têm dono; a do setor é uma por setor, sem dono (M8)."""

    tipo = models.CharField(max_length=12, choices=TipoConversa.choices, default=TipoConversa.INDIVIDUAL)
    titulo = models.CharField("título", max_length=80, blank=True)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="conversas_iativa", null=True, blank=True
    )
    setor = models.ForeignKey("iativa_accounts.Department", on_delete=models.PROTECT, related_name="+")
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)
    encerrada_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "conversa"
        verbose_name_plural = "conversas"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(tipo="setor", usuario__isnull=True) | (~models.Q(tipo="setor") & models.Q(usuario__isnull=False)),
                name="conversa_dono_coerente",
            ),
            models.UniqueConstraint(fields=["setor"], condition=models.Q(tipo="setor"), name="uma_conversa_por_setor"),
            models.UniqueConstraint(fields=["usuario"], condition=models.Q(tipo="mestre"), name="um_mestre_por_usuario"),
        ]

    def __str__(self):
        return f"Conversa #{self.pk} ({self.tipo})"


class Papel(models.TextChoices):
    USUARIO = "usuario", "Usuário"
    IA = "ia", "IAtiva"


class Mensagem(models.Model):
    """Histórico da conversa (M8). Guarda texto e a ficha de critérios; nunca resultados nem SQL."""

    conversa = models.ForeignKey(ConversationContext, on_delete=models.CASCADE, related_name="mensagens")
    papel = models.CharField(max_length=10, choices=Papel.choices)
    autor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    texto = models.TextField(max_length=4000)
    ficha = models.JSONField(default=dict, blank=True)
    solicitacao = models.ForeignKey(
        "RequestContext", on_delete=models.SET_NULL, null=True, blank=True, related_name="mensagens"
    )
    criado_em = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["criado_em", "id"]
        verbose_name = "mensagem"
        verbose_name_plural = "mensagens"


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


class PedidoInterrompido(models.Model):
    """Botão "parar" do chat: marca o envio (id gerado no navegador) que a pessoa interrompeu.
    O envio confere a marca depois da resposta da IA e, se houver, descarta tudo o que faria.
    Guarda só o id e o dono; apagado ao ser usado ou depois de um dia."""

    pedido = models.UUIDField()
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["pedido", "usuario"], name="pedido_interrompido_unico")]
