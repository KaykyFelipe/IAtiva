"""Resolução do valor de um parâmetro pela política de precedência (D12).

1. Regras obrigatórias aprovadas (setor do usuário ou empresa) não são sobrepostas.
   Duas obrigatórias divergentes, ou um valor explícito da solicitação que contrarie a
   obrigatória, geram conflito: o sistema pergunta em vez de escolher.
2. Sem obrigatória: solicitação > privada do usuário > setor > empresa.
   Valores divergentes no mesmo nível geram conflito.
3. Só contam valores aprovados, dentro da validade e não expirados.

O resultado informa a origem (escopo e id) de cada critério.
"""
import json
from dataclasses import dataclass

from django.db.models import Q
from django.utils import timezone

from iativa.accounts.authz import setor_do_usuario

from .models import PRECEDENCIA, Escopo, ParameterBinding, Situacao

RESOLVIDO = "resolvido"
AUSENTE = "ausente"
CONFLITO = "conflito"


@dataclass(frozen=True)
class Resolucao:
    definicao: str
    situacao: str
    valor: object = None
    escopo: str = ""
    binding_id: int | None = None
    motivo: str = ""


def _chave(valor) -> str:
    return json.dumps(valor, sort_keys=True, ensure_ascii=False)


def _candidatos(definicao, user, solicitacao, agora):
    setor = solicitacao.setor if solicitacao is not None else setor_do_usuario(user)
    por_escopo = Q(escopo=Escopo.USUARIO, usuario=user) | Q(escopo=Escopo.EMPRESA)
    if solicitacao is not None:
        por_escopo |= Q(escopo=Escopo.SOLICITACAO, solicitacao=solicitacao)
    if setor is not None:
        por_escopo |= Q(escopo=Escopo.SETOR, setor=setor)
    qs = (
        ParameterBinding.objects.filter(definicao=definicao, situacao=Situacao.APROVADA)
        .filter(por_escopo)
        .filter(Q(valido_ate__isnull=True) | Q(valido_ate__gt=agora))
        .filter(Q(expira_em__isnull=True) | Q(expira_em__gt=agora))
        .order_by("id")
    )
    return list(qs)


def resolver(definicao, user, solicitacao=None, agora=None) -> Resolucao:
    agora = agora or timezone.now()
    codigo = definicao.codigo
    if not definicao.ativo:
        return Resolucao(codigo, AUSENTE, motivo="definicao_inativa")
    candidatos = _candidatos(definicao, user, solicitacao, agora)

    obrigatorias = [b for b in candidatos if b.obrigatoria]
    if obrigatorias:
        if len({_chave(b.valor) for b in obrigatorias}) > 1:
            return Resolucao(codigo, CONFLITO, motivo="obrigatorias_divergentes")
        # Mais específica primeiro (setor antes de empresa) para informar a origem.
        regra = sorted(obrigatorias, key=lambda b: PRECEDENCIA.index(b.escopo))[0]
        explicitos = [b for b in candidatos if b.escopo == Escopo.SOLICITACAO]
        if any(_chave(b.valor) != _chave(regra.valor) for b in explicitos):
            return Resolucao(codigo, CONFLITO, motivo="solicitacao_contraria_regra_obrigatoria")
        return Resolucao(codigo, RESOLVIDO, regra.valor, regra.escopo, regra.pk, "regra_obrigatoria")

    for escopo in PRECEDENCIA:
        nivel = [b for b in candidatos if b.escopo == escopo]
        if not nivel:
            continue
        if len({_chave(b.valor) for b in nivel}) > 1:
            return Resolucao(codigo, CONFLITO, escopo=escopo, motivo="valores_divergentes_no_mesmo_nivel")
        escolhido = nivel[-1]
        return Resolucao(codigo, RESOLVIDO, escolhido.valor, escopo, escolhido.pk, "precedencia")

    return Resolucao(codigo, AUSENTE, motivo="sem_valor_valido")
