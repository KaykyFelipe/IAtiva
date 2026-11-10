"""Serviços de solicitação: criação, transições, cancelamento e checagem de completude."""
from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from iativa.accounts.authz import (
    Permissao,
    PermissaoNegada,
    exigir,
    filiais_autorizadas,
    setor_do_usuario,
    tem_permissao,
)
from iativa.audit.servicos import registrar

from .estados import EM_PREPARACAO, FINAIS, TransicaoInvalida, pode_transicionar
from .models import ConversationContext, Dominio, EstadoSolicitacao as E, RequestContext


@transaction.atomic
def criar_solicitacao(ator, dominio: str, objetivo: str, conversa: ConversationContext | None = None) -> RequestContext:
    setor = setor_do_usuario(ator)
    if setor is None:
        raise PermissaoNegada("Usuário sem setor ativo.")
    exigir(ator, Permissao.CONSULTAR, setor)
    if dominio not in Dominio.values:
        raise ValidationError("Domínio desconhecido.")
    if conversa is None:
        conversa = ConversationContext.objects.create(usuario=ator, setor=setor)
    elif conversa.encerrada_em is not None or (
        conversa.usuario_id != ator.pk if conversa.usuario_id else conversa.setor_id != setor.pk
    ):
        # Individual/mestre: só o dono. Conversa do setor: quem é do setor (M8).
        raise PermissaoNegada("Conversa indisponível.")
    solicitacao = RequestContext(conversa=conversa, usuario=ator, setor=setor, dominio=dominio, objetivo=objetivo)
    solicitacao.full_clean()
    solicitacao.save()
    registrar(
        "solicitacao_criada", "solicitacao", usuario=ator, setor=setor, entidade_id=solicitacao.pk,
        estado_novo=solicitacao.estado,
    )
    return solicitacao


@transaction.atomic
def transicionar(ator, solicitacao: RequestContext, novo: str) -> RequestContext:
    if solicitacao.usuario_id != getattr(ator, "pk", None):
        raise PermissaoNegada("Somente o dono altera a solicitação.")
    solicitacao = RequestContext.objects.select_for_update().get(pk=solicitacao.pk)
    anterior = solicitacao.estado
    if not pode_transicionar(anterior, novo):
        raise TransicaoInvalida(f"{anterior} → {novo} não é permitido.")
    solicitacao.estado = novo
    if novo in FINAIS:
        solicitacao.encerrado_em = timezone.now()
    solicitacao.save(update_fields=["estado", "encerrado_em", "atualizado_em"])
    registrar(
        "transicao_estado", "solicitacao", usuario=ator, setor=solicitacao.setor, entidade_id=solicitacao.pk,
        estado_anterior=anterior, estado_novo=novo,
    )
    return solicitacao


def cancelar(ator, solicitacao: RequestContext) -> RequestContext:
    if solicitacao.estado not in EM_PREPARACAO:
        raise TransicaoInvalida("Só é possível cancelar uma solicitação em preparação.")
    return transicionar(ator, solicitacao, E.CANCELADA)


def pode_ver_solicitacao(user, solicitacao: RequestContext) -> bool:
    if solicitacao.usuario_id == getattr(user, "pk", None) and user.is_active:
        return True
    return solicitacao.compartilhado and tem_permissao(user, Permissao.LER_MEMORIA, solicitacao.setor)


@dataclass
class Avaliacao:
    estado: str
    resolucoes: list = field(default_factory=list)
    pendencias: list = field(default_factory=list)
    motivo: str = ""


def avaliar_completude(ator, solicitacao: RequestContext, definicoes) -> Avaliacao:
    """Checagem determinística antes da execução.

    Bloqueia sem permissão de consulta ou sem filiais; volta para `aguardando_parametros` se
    algum parâmetro obrigatório estiver ausente, inválido, expirado ou em conflito.
    """
    from iativa.memory.resolucao import RESOLVIDO, resolver

    if solicitacao.estado not in (E.RASCUNHO, E.AGUARDANDO_PARAMETROS, E.BLOQUEADA):
        raise TransicaoInvalida("A solicitação não está em preparação para validação.")
    solicitacao = transicionar(ator, solicitacao, E.VALIDANDO)

    if not tem_permissao(ator, Permissao.CONSULTAR, solicitacao.setor) or not filiais_autorizadas(
        ator, solicitacao.setor
    ):
        registrar(
            "acesso_negado", "solicitacao", usuario=ator, setor=solicitacao.setor, entidade_id=solicitacao.pk,
            estado_novo="consultar", resultado="negado",
        )
        transicionar(ator, solicitacao, E.BLOQUEADA)
        return Avaliacao(estado=E.BLOQUEADA, motivo="sem_autorizacao")

    resolucoes = [resolver(definicao, ator, solicitacao) for definicao in definicoes]
    pendencias = [r for r in resolucoes if r.situacao != RESOLVIDO]
    if pendencias:
        transicionar(ator, solicitacao, E.AGUARDANDO_PARAMETROS)
        return Avaliacao(estado=E.AGUARDANDO_PARAMETROS, resolucoes=resolucoes, pendencias=pendencias)
    transicionar(ator, solicitacao, E.PRONTA)
    return Avaliacao(estado=E.PRONTA, resolucoes=resolucoes)
