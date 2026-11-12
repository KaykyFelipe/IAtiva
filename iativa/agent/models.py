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
    REMOVIDA = "removida", "Removida"


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


class EscopoNota(models.TextChoices):
    USUARIO = "usuario", "Só do usuário"
    SETOR = "setor", "Setor"
    EMPRESA = "empresa", "Toda a empresa"


class SituacaoNota(models.TextChoices):
    PROPOSTA = "proposta", "Aguardando aprovação"
    APROVADA = "aprovada", "Aprovada"
    REJEITADA = "rejeitada", "Rejeitada"
    SUBSTITUIDA = "substituida", "Substituída"


class SituacaoTarefa(models.TextChoices):
    PENDENTE = "pendente", "Na fila"
    EXECUTANDO = "executando", "Em execução"
    CONCLUIDA = "concluida", "Concluída"
    LIMITE = "limite", "Parou num limite"
    BLOQUEADA = "bloqueada", "Bloqueada"
    AGUARDANDO = "aguardando_usuario", "Aguardando você"
    CANCELADA = "cancelada", "Cancelada"
    FALHOU = "falhou", "Falhou"


TERMINAIS = (SituacaoTarefa.CONCLUIDA, SituacaoTarefa.LIMITE, SituacaoTarefa.BLOQUEADA, SituacaoTarefa.AGUARDANDO,
             SituacaoTarefa.CANCELADA, SituacaoTarefa.FALHOU)


class Tarefa(models.Model):
    """Tarefa longa do agente (M25), executada pelo worker fora da requisição HTTP.

    Guarda só o permitido: objetivo estruturado, critérios já exibidos no histórico, plano (nomes de
    campos), estado das etapas, limites, consumo, contagens de cobertura e códigos de situação.
    Nunca linhas, nomes, documentos ou números do ERP: as evidências ficam só na memória da execução."""

    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    conversa = models.ForeignKey("iativa_chat.ConversationContext", on_delete=models.CASCADE, related_name="tarefas")
    solicitacao_base = models.ForeignKey("iativa_chat.RequestContext", on_delete=models.SET_NULL, null=True,
                                         related_name="+")
    tipo = models.CharField(max_length=30, default="investigar_vinculos")
    objetivo = models.CharField(max_length=300)
    escopo = models.JSONField(default=dict)  # consulta de origem e critérios (os mesmos do histórico)
    criterio_conclusao = models.CharField(max_length=300)
    proposta = models.JSONField(default=dict)  # escolha do modelo (nomes de campos)
    plano = models.JSONField(default=dict)  # plano validado no dicionário
    etapas = models.JSONField(default=list)
    limites = models.JSONField(default=dict)
    consumo = models.JSONField(default=dict)
    cobertura = models.JSONField(default=dict)  # só contagens
    situacao = models.CharField(max_length=20, choices=SituacaoTarefa.choices, default=SituacaoTarefa.PENDENTE)
    motivo = models.CharField(max_length=400, blank=True)
    progresso = models.CharField(max_length=300, blank=True)
    cancelar_solicitado = models.BooleanField(default=False)
    execucao = models.CharField(max_length=32, blank=True)  # identifica quem está executando agora
    lease_ate = models.DateTimeField(null=True, blank=True)
    execucoes = models.PositiveIntegerField(default=0)
    reconstruida = models.BooleanField(default=False)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)
    iniciado_em = models.DateTimeField(null=True, blank=True)
    concluido_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-id"]
        indexes = [models.Index(fields=["situacao", "lease_ate"])]

    @property
    def terminal(self) -> bool:
        return self.situacao in TERMINAIS


class TipoNota(models.TextChoices):
    PREFERENCIA = "preferencia", "Preferência de apresentação"
    TERMO = "termo", "Definição de termo"
    REGRA = "regra", "Regra de negócio"
    CORRECAO = "correcao", "Correção reutilizável"
    HIPOTESE = "hipotese", "Hipótese não confirmada"


class NotaAgente(models.Model):
    """Memória do agente (M10/Etapa 6): frases curtas que o usuário ensinou (termos, preferências,
    como interpretar perguntas). Vão ao modelo como referência. Nunca resultados, SQL ou CPF/CNPJ.

    Nota do usuário vale na hora. Nota do setor ou da empresa entra como proposta e só vale depois
    de aprovada por quem tem "aprovar definições" naquele nível. `substitui` aponta para a nota
    aprovada com que a proposta conflita (mesmo termo); aprovar a proposta substitui a anterior."""

    texto = models.CharField(max_length=300)
    escopo = models.CharField(max_length=10, choices=EscopoNota.choices)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True,
                                related_name="+")
    setor = models.ForeignKey("iativa_accounts.Department", on_delete=models.PROTECT, null=True, blank=True,
                              related_name="+")
    situacao = models.CharField(max_length=12, choices=SituacaoNota.choices, default=SituacaoNota.APROVADA)
    substitui = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                   related_name="+")
    aprovado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name="+")
    aprovado_em = models.DateTimeField(null=True, blank=True)
    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    # M22: o que a nota é, até quando vale e, nas regras, o critério estruturado que o backend aplica
    # ({"tabela", "campo", "op", "valor"}). Hipótese nunca é aplicada: só vai ao modelo como dúvida.
    tipo = models.CharField(max_length=12, choices=TipoNota.choices, default=TipoNota.TERMO)
    criterios = models.JSONField(default=dict, blank=True)
    valido_ate = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-criado_em"]
        verbose_name = "nota da memória do agente"
        verbose_name_plural = "memória do agente"
        constraints = [
            models.CheckConstraint(
                condition=(models.Q(escopo="usuario", usuario__isnull=False, setor__isnull=True)
                           | models.Q(escopo="setor", usuario__isnull=True, setor__isnull=False)
                           | models.Q(escopo="empresa", usuario__isnull=True, setor__isnull=True)),
                name="nota_escopo_coerente_v2",
            )
        ]

    def __str__(self):
        return self.texto[:60]
