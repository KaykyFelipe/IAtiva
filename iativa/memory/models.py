"""Memória estruturada de parâmetros (Etapa 2; PEDIDO_INICIAL §8 e PLANO §3–4).

- `ParameterDefinition`: o que é o parâmetro (ex.: "data de corte"). Reutilizável.
- `ParameterBinding`: um valor do parâmetro num escopo (solicitação, usuário, setor ou empresa),
  com origem, aprovação, validade e versão. O valor escolhido numa solicitação fica no escopo
  da solicitação e não vira padrão de ninguém.
- `MemoryRevision`: histórico de criação, aprovação, rejeição, correção e revogação.

Nunca guarda dados do ERP, SQL, resultados ou texto de conversa.
"""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class TipoValor(models.TextChoices):
    DATA = "data", "Data"
    PERIODO = "periodo", "Período"
    FILIAL = "filial", "Filial"
    TEXTO = "texto", "Texto curto"
    NUMERO = "numero", "Número"
    BOOLEANO = "booleano", "Sim/não"
    ESCOLHA = "escolha", "Escolha"


class ParameterDefinition(models.Model):
    codigo = models.SlugField("código", max_length=60, unique=True)
    nome = models.CharField(max_length=120)
    descricao = models.TextField("descrição", max_length=1000, blank=True)
    tipo = models.CharField(max_length=20, choices=TipoValor.choices)
    opcoes = models.JSONField("opções", default=list, blank=True, help_text="Somente para o tipo escolha.")
    contem_dado_pessoal = models.BooleanField(
        "contém dado pessoal",
        default=False,
        help_text="Valores ficam só no escopo da solicitação, por 30 dias, e nunca viram padrão.",
    )
    ativo = models.BooleanField(default=True)
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["codigo"]
        verbose_name = "definição de parâmetro"
        verbose_name_plural = "definições de parâmetros"

    def __str__(self):
        return self.nome

    def clean(self):
        opcoes = self.opcoes
        if self.tipo == TipoValor.ESCOLHA:
            if not isinstance(opcoes, list) or not opcoes or not all(isinstance(o, str) and o for o in opcoes):
                raise ValidationError({"opcoes": "Informe a lista de opções."})
            if len(set(opcoes)) != len(opcoes):
                raise ValidationError({"opcoes": "Opções repetidas."})
        elif opcoes:
            raise ValidationError({"opcoes": "Opções só se aplicam ao tipo escolha."})


class Escopo(models.TextChoices):
    SOLICITACAO = "solicitacao", "Solicitação"
    USUARIO = "usuario", "Usuário (privado)"
    SETOR = "setor", "Setor"
    EMPRESA = "empresa", "Empresa"


# Precedência para preferências (D12): mais específico primeiro.
PRECEDENCIA = (Escopo.SOLICITACAO, Escopo.USUARIO, Escopo.SETOR, Escopo.EMPRESA)
ESCOPOS_COMPARTILHADOS = frozenset({Escopo.SETOR, Escopo.EMPRESA})


class Situacao(models.TextChoices):
    PROPOSTA = "proposta", "Proposta"
    APROVADA = "aprovada", "Aprovada"
    REJEITADA = "rejeitada", "Rejeitada"
    SUBSTITUIDA = "substituida", "Substituída"
    REVOGADA = "revogada", "Revogada"


class Origem(models.TextChoices):
    CHAT = "chat", "Informado no chat"
    ADMINISTRACAO = "administracao", "Cadastro administrativo"
    DOCUMENTO = "documento", "Documento aprovado"


class ParameterBinding(models.Model):
    definicao = models.ForeignKey(
        ParameterDefinition, on_delete=models.PROTECT, related_name="valores", verbose_name="definição"
    )
    escopo = models.CharField(max_length=20, choices=Escopo.choices)
    solicitacao = models.ForeignKey(
        "iativa_chat.RequestContext", on_delete=models.CASCADE, null=True, blank=True, related_name="parametros"
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name="memoria_privada"
    )
    setor = models.ForeignKey(
        "iativa_accounts.Department", on_delete=models.PROTECT, null=True, blank=True, related_name="memoria"
    )
    valor = models.JSONField()
    obrigatoria = models.BooleanField(
        "regra obrigatória", default=False, help_text="Somente setor/empresa. Não é sobreposta por preferências."
    )
    situacao = models.CharField("situação", max_length=20, choices=Situacao.choices)
    origem = models.CharField(max_length=20, choices=Origem.choices, default=Origem.CHAT)
    evidencia = models.CharField(
        "evidência", max_length=300, blank=True,
        help_text="Referência curta à fonte autorizada (ex.: política de cobrança v3). Nunca dados do ERP.",
    )
    valido_ate = models.DateTimeField("válido até", null=True, blank=True)
    versao = models.PositiveIntegerField("versão", default=1)
    substitui = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="substituida_por"
    )
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    aprovado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    aprovado_em = models.DateTimeField(null=True, blank=True)
    expira_em = models.DateTimeField(
        null=True, blank=True, help_text="Descarte obrigatório por retenção (dados pessoais)."
    )

    class Meta:
        verbose_name = "valor de parâmetro"
        verbose_name_plural = "valores de parâmetros"
        indexes = [models.Index(fields=["definicao", "escopo", "situacao"])]
        constraints = [
            models.CheckConstraint(
                name="binding_escopo_coerente",
                condition=(
                    Q(escopo=Escopo.SOLICITACAO, solicitacao__isnull=False, usuario__isnull=True, setor__isnull=True)
                    | Q(escopo=Escopo.USUARIO, solicitacao__isnull=True, usuario__isnull=False, setor__isnull=True)
                    | Q(escopo=Escopo.SETOR, solicitacao__isnull=True, usuario__isnull=True, setor__isnull=False)
                    | Q(escopo=Escopo.EMPRESA, solicitacao__isnull=True, usuario__isnull=True, setor__isnull=True)
                ),
            ),
            models.CheckConstraint(
                name="binding_obrigatoria_so_compartilhada",
                condition=Q(obrigatoria=False) | Q(escopo__in=[Escopo.SETOR, Escopo.EMPRESA]),
            ),
        ]

    def __str__(self):
        return f"{self.definicao.codigo} [{self.escopo}] v{self.versao} ({self.situacao})"


class AcaoRevisao(models.TextChoices):
    CRIADA = "criada", "Criada"
    APROVADA = "aprovada", "Aprovada"
    REJEITADA = "rejeitada", "Rejeitada"
    CORRIGIDA = "corrigida", "Corrigida"
    REVOGADA = "revogada", "Revogada"


class MemoryRevision(models.Model):
    binding = models.ForeignKey(ParameterBinding, on_delete=models.CASCADE, related_name="revisoes")
    acao = models.CharField("ação", max_length=20, choices=AcaoRevisao.choices)
    situacao_anterior = models.CharField(max_length=20, choices=Situacao.choices, blank=True)
    situacao_nova = models.CharField(max_length=20, choices=Situacao.choices)
    autor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    motivo = models.CharField(max_length=300, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["criado_em", "id"]
        verbose_name = "revisão de memória"
        verbose_name_plural = "revisões de memória"

    def __str__(self):
        return f"{self.binding_id} {self.acao}"
