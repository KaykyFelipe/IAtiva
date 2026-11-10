"""Conversas com histórico (M8): acesso, lateral e envio de mensagens.

Regras de acesso (todas no backend):
- individual e mestre: só o dono; o mestre exige "consultar" em mais de um setor;
- setor: lê quem tem "consultar" ou "ler memória" no setor; escreve quem é do setor e tem "consultar".

Guarda texto e fichas de critérios. Nunca resultados de consultas nem SQL.
"""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from iativa.accounts.authz import Permissao, PermissaoNegada, setor_do_usuario, setores_autorizados, tem_permissao
from iativa.accounts.models import Department
from iativa.agent import orquestrador
from iativa.memory.validadores import contem_cpf_cnpj

from .estados import EM_PREPARACAO
from .models import ConversationContext, Mensagem, Papel, RequestContext, TipoConversa as T
from .servicos import cancelar

MAX_TEXTO = 4000
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro",
         "outubro", "novembro", "dezembro"]


def setores_leitura(user) -> set[int]:
    return setores_autorizados(user, Permissao.CONSULTAR) | setores_autorizados(user, Permissao.LER_MEMORIA)


def pode_mestre(user) -> bool:
    return len(setores_autorizados(user, Permissao.CONSULTAR)) > 1


def pode_ver(user, c: ConversationContext) -> bool:
    if c.tipo == T.SETOR:
        return c.setor_id in setores_leitura(user)
    if c.tipo == T.MESTRE and not pode_mestre(user):
        return False
    return c.usuario_id == user.pk


def pode_escrever(user, c: ConversationContext) -> bool:
    if c.tipo == T.SETOR:
        setor = setor_do_usuario(user)
        return setor is not None and setor.pk == c.setor_id and tem_permissao(user, Permissao.CONSULTAR, setor)
    return pode_ver(user, c)


def obter(user, conversa_id) -> ConversationContext:
    c = ConversationContext.objects.select_related("setor").filter(pk=conversa_id, encerrada_em__isnull=True).first()
    if c is None or not pode_ver(user, c):
        raise PermissaoNegada("Conversa indisponível.")
    return c


def nova_individual(user) -> ConversationContext:
    setor = setor_do_usuario(user)
    if setor is None:
        raise PermissaoNegada("Usuário sem setor ativo.")
    return ConversationContext.objects.create(tipo=T.INDIVIDUAL, usuario=user, setor=setor)


def do_setor(user, setor_id) -> ConversationContext:
    if setor_id not in setores_leitura(user):
        raise PermissaoNegada("Sem acesso ao setor.")
    setor = Department.objects.get(pk=setor_id, ativo=True)
    c, _ = ConversationContext.objects.get_or_create(tipo=T.SETOR, setor=setor, defaults={"titulo": setor.nome})
    return c


def mestre(user) -> ConversationContext:
    setor = setor_do_usuario(user)
    if setor is None or not pode_mestre(user):
        raise PermissaoNegada("Chat mestre indisponível.")
    c, _ = ConversationContext.objects.get_or_create(
        tipo=T.MESTRE, usuario=user, defaults={"setor": setor, "titulo": "Chat mestre"}
    )
    return c


def _grupo(data, hoje) -> str:
    dias = (hoje - data).days
    if dias <= 0:
        return "Hoje"
    if dias <= 7:
        return "Últimos 7 dias"
    nome = MESES[data.month - 1].capitalize()
    return nome if data.year == hoje.year else f"{nome} de {data.year}"


def lateral(user) -> dict:
    hoje = timezone.localdate()
    individuais = ConversationContext.objects.filter(
        tipo=T.INDIVIDUAL, usuario=user, encerrada_em__isnull=True, mensagens__isnull=False
    ).distinct().order_by("-atualizado_em")[:60]
    grupos: list[tuple[str, list]] = []
    for c in individuais:
        rotulo = _grupo(timezone.localdate(c.atualizado_em), hoje)
        if not grupos or grupos[-1][0] != rotulo:
            grupos.append((rotulo, []))
        grupos[-1][1].append({"conversa": c, "estado": _estado_lateral(c)})
    setores = Department.objects.filter(pk__in=setores_leitura(user), ativo=True).order_by("nome")
    return {"grupos": grupos, "setores": setores, "mestre": pode_mestre(user)}


def _estado_lateral(c) -> str:
    s = c.solicitacoes.filter(estado__in=list(EM_PREPARACAO)).order_by("-id").first()
    if s is None:
        return ""
    return "pronta" if s.estado == "pronta" else "pendente"


def _ativa(user, c) -> RequestContext | None:
    return c.solicitacoes.filter(usuario=user, estado__in=list(EM_PREPARACAO)).order_by("-id").first()


def _ficha(r: orquestrador.Resposta) -> dict:
    return {
        "tipo": r.tipo,
        "objetivo": r.objetivo,
        "estado": r.solicitacao.estado if r.solicitacao else "",
        "estado_nome": r.solicitacao.get_estado_display() if r.solicitacao else "",
        "perguntas": [p.__dict__ for p in r.perguntas],
        "criterios": [c.__dict__ for c in r.criterios],
        "ambiguidade": r.ambiguidade,
        "opcoes": r.opcoes,
        "erros": r.erros,
        "provedor": r.provedor,
    }


def _resposta_ia(c, r: orquestrador.Resposta) -> Mensagem:
    texto = r.mensagem + (f"\n\n{r.observacao}" if r.observacao else "")
    return Mensagem.objects.create(conversa=c, papel=Papel.IA, texto=texto[:MAX_TEXTO], ficha=_ficha(r),
                                   solicitacao=r.solicitacao)


def _validar_texto(texto: str) -> str:
    texto = (texto or "").strip()
    if not texto or len(texto) > MAX_TEXTO:
        raise ValidationError(f"A mensagem deve ter de 1 a {MAX_TEXTO} caracteres.")
    if contem_cpf_cnpj(texto):
        raise ValidationError("Não envie CPF ou CNPJ: a conversa fica salva. Use o código do cliente.")
    return texto


def _tocar(c, texto):
    if not c.titulo:
        c.titulo = texto[:80]
    c.atualizado_em = timezone.now()
    c.save(update_fields=["titulo", "atualizado_em"])


@transaction.atomic
def enviar(user, c: ConversationContext, texto: str, provedor=None) -> None:
    if not pode_escrever(user, c):
        raise PermissaoNegada("Somente leitura nesta conversa.")
    texto = _validar_texto(texto)
    Mensagem.objects.create(conversa=c, papel=Papel.USUARIO, autor=user, texto=texto)
    atual = _ativa(user, c)
    r = orquestrador.processar_mensagem(user, texto, atual.pk if atual else None, provedor=provedor, conversa=c)
    _resposta_ia(c, r)
    _tocar(c, texto)


@transaction.atomic
def responder(user, c: ConversationContext, codigo: str, nome: str, valor: str) -> None:
    if not pode_escrever(user, c):
        raise PermissaoNegada("Somente leitura nesta conversa.")
    valor = _validar_texto(valor)[:200]
    atual = _ativa(user, c)
    Mensagem.objects.create(conversa=c, papel=Papel.USUARIO, autor=user, texto=f"{nome}: {valor}")
    r = orquestrador.responder_parametro(user, atual.pk if atual else None, codigo, valor)
    _resposta_ia(c, r)
    _tocar(c, nome)


@transaction.atomic
def cancelar_atual(user, c: ConversationContext) -> None:
    atual = _ativa(user, c)
    if atual is None:
        return
    s = cancelar(user, atual)
    Mensagem.objects.create(conversa=c, papel=Papel.USUARIO, autor=user, texto="Cancelar a solicitação.")
    _resposta_ia(c, orquestrador.Resposta("cancelada", "Solicitação cancelada.", solicitacao=s))
    _tocar(c, "")


def mensagens_com_interacao(user, c: ConversationContext) -> list[dict]:
    """Histórico para a tela. Só a última ficha de cada solicitação em andamento do próprio
    usuário fica editável; as anteriores aparecem como registro."""
    msgs = list(c.mensagens.select_related("autor", "solicitacao"))
    ultima_por_solicitacao = {}
    for m in msgs:
        if m.solicitacao_id and m.ficha:
            ultima_por_solicitacao[m.solicitacao_id] = m.pk
    out = []
    for m in msgs:
        s = m.solicitacao
        editavel = bool(
            s and ultima_por_solicitacao.get(s.pk) == m.pk and s.usuario_id == user.pk
            and s.estado in ("aguardando_parametros",)
        )
        out.append({"m": m, "editavel": editavel, "proprio": m.autor_id == user.pk})
    return out

