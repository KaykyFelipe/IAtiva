"""Incidentes técnicos (M32): o que deu errado, agrupado pela causa, sem nenhum dado do ERP.

Guarda só: categoria, código, componente/operação, versão, correlação, rastreamento sanitizado
(arquivo:linha:função do código do IAtiva, sem mensagens de erro) e condições técnicas (chaves
operacionais). Nunca texto de mensagens, nomes, documentos, resultados, SQL ou credenciais.
"""
from django.db import models


class Categoria(models.TextChoices):
    ERRO_TECNICO = "erro_tecnico", "Erro técnico reproduzível"
    CAPACIDADE = "capacidade_ausente", "Capacidade técnica ausente"
    TRANSITORIA = "transitoria", "Falha transitória"
    REGRA_NEGOCIO = "regra_negocio", "Informação ou regra de negócio desconhecida"
    PERMISSAO = "permissao", "Falta de permissão"
    FONTE = "limitacao_fonte", "Limitação da fonte de dados"


class SituacaoIncidente(models.TextChoices):
    ABERTO = "aberto", "Aberto"
    NAO_ELEGIVEL = "nao_elegivel", "Não é correção de código"
    EM_CORRECAO = "em_correcao", "Em correção"
    PRONTO = "pronto_publicar", "Correção validada, aguardando publicação"
    PUBLICADO = "publicado", "Correção publicada e conferida"
    FALHOU = "falhou_correcao", "Correção não validada"
    BLOQUEADO = "bloqueado_admin", "Bloqueado: decisão administrativa"


class Incidente(models.Model):
    assinatura = models.CharField(max_length=64, unique=True)  # mesma causa = mesmo incidente
    categoria = models.CharField(max_length=20, choices=Categoria.choices)
    codigo = models.CharField(max_length=80)
    componente = models.CharField(max_length=60)
    operacao = models.CharField(max_length=80)
    versao = models.CharField(max_length=40)
    correlacao = models.CharField(max_length=32)  # id da última ocorrência
    rastreamento = models.JSONField(default=list)  # ["iativa/agent/x.py:120:func", ...]
    condicoes = models.JSONField(default=dict)  # só chaves operacionais (etapa, tipo, situação)
    ocorrencias = models.PositiveIntegerField(default=1)
    primeira_em = models.DateTimeField(auto_now_add=True)
    ultima_em = models.DateTimeField(auto_now=True)
    situacao = models.CharField(max_length=20, choices=SituacaoIncidente.choices, default=SituacaoIncidente.ABERTO)
    tentativas = models.PositiveIntegerField(default=0)
    reaberturas = models.PositiveIntegerField(default=0)
    commit = models.CharField(max_length=40, blank=True)
    versao_corrigida = models.CharField(max_length=40, blank=True)
    motivo = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-ultima_em"]

    def __str__(self):
        return f"#{self.pk} {self.codigo} ({self.get_situacao_display()})"
