"""Registro, aprovação, correção e revogação de valores de parâmetros.

Regras:
- Solicitação e usuário: memória privada do dono, aprovada na criação.
- Setor e empresa: entram como proposta. Uma afirmação no chat não vira regra oficial:
  só vale depois de aprovada por quem tem `pode_aprovar_conhecimento` naquele nível,
  e quem propôs não aprova a própria proposta.
- Parâmetro com dado pessoal: só no escopo da solicitação, com descarte em 30 dias.
- Correção cria nova versão ligada à anterior (`substitui`) e registra revisão.
"""
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from iativa.accounts.authz import Permissao, PermissaoNegada, exigir, setor_do_usuario
from iativa.audit.servicos import registrar
from iativa.chat.estados import EM_PREPARACAO

from .models import (
    ESCOPOS_COMPARTILHADOS,
    AcaoRevisao,
    Escopo,
    MemoryRevision,
    Origem,
    ParameterBinding,
    Situacao,
)
from .validadores import recusar_cpf_cnpj, validar_valor


def _dias_dado_pessoal() -> int:
    return int(settings.IATIVA_RETENCAO["valor_dado_pessoal_dias"])


def _revisar(binding, acao, anterior, ator, motivo=""):
    recusar_cpf_cnpj(motivo)
    MemoryRevision.objects.create(
        binding=binding, acao=acao, situacao_anterior=anterior, situacao_nova=binding.situacao, autor=ator,
        motivo=motivo[:300],
    )


def _auditar(acao, ator, binding, anterior=""):
    registrar(
        acao, "parametro", usuario=ator, setor=binding.setor, entidade_id=binding.pk,
        estado_anterior=anterior, estado_novo=binding.situacao,
    )


def _exigir_dono_ou_aprovador(ator, binding):
    """Memória privada: só o dono. Compartilhada: quem aprova naquele nível."""
    if binding.escopo == Escopo.SOLICITACAO:
        if binding.solicitacao.usuario_id != ator.pk:
            raise PermissaoNegada("Somente o dono altera parâmetros da solicitação.")
    elif binding.escopo == Escopo.USUARIO:
        if binding.usuario_id != ator.pk:
            raise PermissaoNegada("Memória privada de outro usuário.")
    else:
        exigir(ator, Permissao.APROVAR_CONHECIMENTO, binding.setor)


@transaction.atomic
def registrar_valor(
    ator,
    definicao,
    escopo: str,
    valor,
    *,
    solicitacao=None,
    setor=None,
    obrigatoria: bool = False,
    valido_ate=None,
    origem: str = Origem.CHAT,
    evidencia: str = "",
    substitui: ParameterBinding | None = None,
) -> ParameterBinding:
    if not definicao.ativo:
        raise ValidationError("Definição inativa.")
    validar_valor(definicao, valor)
    recusar_cpf_cnpj(evidencia)
    if definicao.contem_dado_pessoal and escopo != Escopo.SOLICITACAO:
        raise ValidationError("Parâmetro com dado pessoal só pode ser usado na solicitação e nunca vira padrão.")
    if obrigatoria and escopo not in ESCOPOS_COMPARTILHADOS:
        raise ValidationError("Regra obrigatória só existe no setor ou na empresa.")
    agora = timezone.now()
    if valido_ate is not None and valido_ate <= agora:
        raise ValidationError("A validade deve ser futura.")

    usuario = None
    if escopo == Escopo.SOLICITACAO:
        if solicitacao is None or solicitacao.usuario_id != ator.pk:
            raise PermissaoNegada("Somente o dono informa parâmetros da solicitação.")
        if solicitacao.estado not in EM_PREPARACAO:
            raise ValidationError("A solicitação não está em preparação.")
        setor = None
    elif escopo == Escopo.USUARIO:
        usuario, solicitacao, setor = ator, None, None
    elif escopo == Escopo.SETOR:
        solicitacao = None
        if setor is None:
            raise ValidationError("Informe o setor.")
        if setor_do_usuario(ator) != setor:
            exigir(ator, Permissao.APROVAR_CONHECIMENTO, setor)
    elif escopo == Escopo.EMPRESA:
        solicitacao, setor = None, None
        if setor_do_usuario(ator) is None:
            raise PermissaoNegada("Usuário sem setor ativo.")
    else:
        raise ValidationError("Escopo desconhecido.")

    privado = escopo not in ESCOPOS_COMPARTILHADOS
    binding = ParameterBinding.objects.create(
        definicao=definicao,
        escopo=escopo,
        solicitacao=solicitacao,
        usuario=usuario,
        setor=setor,
        valor=valor,
        obrigatoria=obrigatoria,
        situacao=Situacao.APROVADA if privado else Situacao.PROPOSTA,
        origem=origem,
        evidencia=evidencia,
        valido_ate=valido_ate,
        versao=(substitui.versao + 1) if substitui else 1,
        substitui=substitui,
        criado_por=ator,
        aprovado_por=ator if privado else None,
        aprovado_em=agora if privado else None,
        expira_em=agora + timedelta(days=_dias_dado_pessoal()) if definicao.contem_dado_pessoal else None,
    )
    _revisar(binding, AcaoRevisao.CRIADA, "", ator)
    _auditar("parametro_registrado", ator, binding)
    return binding


def _travar(binding) -> ParameterBinding:
    # `of=("self",)`: o PostgreSQL não aceita FOR UPDATE no lado anulável de um JOIN.
    return (
        ParameterBinding.objects.select_for_update(of=("self",))
        .select_related("solicitacao", "setor", "definicao")
        .get(pk=binding.pk)
    )


@transaction.atomic
def aprovar(ator, binding: ParameterBinding, motivo: str = "") -> ParameterBinding:
    binding = _travar(binding)
    if binding.situacao != Situacao.PROPOSTA:
        raise ValidationError("Somente propostas podem ser aprovadas.")
    exigir(ator, Permissao.APROVAR_CONHECIMENTO, binding.setor)
    if binding.criado_por_id == ator.pk:
        raise PermissaoNegada("Quem propôs não aprova a própria proposta.")
    binding.situacao = Situacao.APROVADA
    binding.aprovado_por = ator
    binding.aprovado_em = timezone.now()
    binding.save(update_fields=["situacao", "aprovado_por", "aprovado_em"])
    _revisar(binding, AcaoRevisao.APROVADA, Situacao.PROPOSTA, ator, motivo)
    _auditar("parametro_aprovado", ator, binding, Situacao.PROPOSTA)
    if binding.substitui_id:
        anterior = _travar(binding.substitui)
        if anterior.situacao == Situacao.APROVADA:
            anterior.situacao = Situacao.SUBSTITUIDA
            anterior.save(update_fields=["situacao"])
            _revisar(anterior, AcaoRevisao.CORRIGIDA, Situacao.APROVADA, ator, motivo)
    return binding


@transaction.atomic
def rejeitar(ator, binding: ParameterBinding, motivo: str = "") -> ParameterBinding:
    binding = _travar(binding)
    if binding.situacao != Situacao.PROPOSTA:
        raise ValidationError("Somente propostas podem ser rejeitadas.")
    exigir(ator, Permissao.APROVAR_CONHECIMENTO, binding.setor)
    binding.situacao = Situacao.REJEITADA
    binding.save(update_fields=["situacao"])
    _revisar(binding, AcaoRevisao.REJEITADA, Situacao.PROPOSTA, ator, motivo)
    _auditar("parametro_rejeitado", ator, binding, Situacao.PROPOSTA)
    return binding


@transaction.atomic
def corrigir(ator, binding: ParameterBinding, novo_valor, motivo: str = "", valido_ate=None) -> ParameterBinding:
    """Cria nova versão. Privada: vale na hora. Compartilhada: proposta; a anterior vale até a aprovação."""
    binding = _travar(binding)
    if binding.situacao != Situacao.APROVADA:
        raise ValidationError("Somente valores aprovados podem ser corrigidos.")
    privado = binding.escopo not in ESCOPOS_COMPARTILHADOS
    if privado:
        _exigir_dono_ou_aprovador(ator, binding)
    novo = registrar_valor(
        ator,
        binding.definicao,
        binding.escopo,
        novo_valor,
        solicitacao=binding.solicitacao,
        setor=binding.setor,
        obrigatoria=binding.obrigatoria,
        valido_ate=valido_ate,
        origem=binding.origem,
        evidencia=binding.evidencia,
        substitui=binding,
    )
    if privado:
        binding.situacao = Situacao.SUBSTITUIDA
        binding.save(update_fields=["situacao"])
        _revisar(binding, AcaoRevisao.CORRIGIDA, Situacao.APROVADA, ator, motivo)
    _auditar("parametro_corrigido", ator, novo)
    return novo


@transaction.atomic
def revogar(ator, binding: ParameterBinding, motivo: str = "") -> ParameterBinding:
    binding = _travar(binding)
    if binding.situacao not in (Situacao.APROVADA, Situacao.PROPOSTA):
        raise ValidationError("Valor já encerrado.")
    _exigir_dono_ou_aprovador(ator, binding)
    anterior = binding.situacao
    binding.situacao = Situacao.REVOGADA
    binding.save(update_fields=["situacao"])
    _revisar(binding, AcaoRevisao.REVOGADA, anterior, ator, motivo)
    _auditar("parametro_revogado", ator, binding, anterior)
    return binding
