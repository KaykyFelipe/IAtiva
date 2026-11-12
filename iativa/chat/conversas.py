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
    from iativa.agent import memoria

    pendentes = sum(1 for p in memoria.pendencias(user) if p.pode_decidir)
    from iativa.agent.claude_login import permitido

    return {"grupos": grupos, "setores": setores, "mestre": pode_mestre(user), "pendentes": pendentes,
            "claude": permitido(user)}


def renomear(user, conversa_id, titulo: str) -> ConversationContext:
    """Só o dono renomeia a própria conversa individual."""
    c = ConversationContext.objects.get(pk=conversa_id, tipo=T.INDIVIDUAL, usuario=user)
    titulo = " ".join(titulo.split())[:80]
    if not titulo:
        raise ValidationError("Informe um nome para a conversa.")
    c.titulo = titulo
    c.save(update_fields=["titulo"])
    return c


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
        "candidatos": r.candidatos,  # só metadados do dicionário (M9)
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


def _verificador(user, pedido):
    if not pedido:
        return None
    from .models import PedidoInterrompido

    return lambda: PedidoInterrompido.objects.filter(pedido=pedido, usuario=user).exists()


def interromper(user, pedido) -> None:
    """Botão "parar": marca o envio; `enviar` desiste se ainda não agiu."""
    from datetime import timedelta

    from .models import PedidoInterrompido

    PedidoInterrompido.objects.filter(criado_em__lt=timezone.now() - timedelta(days=1)).delete()
    PedidoInterrompido.objects.get_or_create(pedido=pedido, usuario=user)


def limpar_interrupcao(user, pedido) -> None:
    from .models import PedidoInterrompido

    if pedido:
        PedidoInterrompido.objects.filter(pedido=pedido, usuario=user).delete()


@transaction.atomic
def enviar(user, c: ConversationContext, texto: str, provedor=None, pedido=None, resultados=None) -> None:
    """`pedido`: id do envio gerado no navegador; se a pessoa clicar em "parar" antes da IA agir,
    `agente.Interrompido` é lançado e a transação desfaz tudo (inclusive a mensagem dela)."""
    if not pode_escrever(user, c):
        raise PermissaoNegada("Somente leitura nesta conversa.")
    from iativa.agent import agente

    texto = _validar_texto(texto)
    m = Mensagem.objects.create(conversa=c, papel=Papel.USUARIO, autor=user, texto=texto)
    saida = agente.conversar(user, c, texto, provedor=provedor, mensagem_usuario_id=m.pk,
                             interrompido=_verificador(user, pedido), resultados=resultados or [])
    msg = Mensagem.objects.create(conversa=c, papel=Papel.IA, texto=saida.texto[:MAX_TEXTO], ficha=saida.ficha,
                                  solicitacao=saida.solicitacao)
    _tocar(c, texto)
    return msg, saida.resultado


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


def _executar_e_registrar(user, c, s):
    from iativa.query import servico

    res = servico.executar_solicitacao(user, s)
    if res.ok:
        # M8: o histórico guarda só que a consulta rodou e quantas linhas; nunca os dados.
        texto = (f"Consulta executada em {timezone.localtime():%d/%m/%Y às %H:%M}: {len(res.linhas)} linha(s). "
                 "Os dados não ficam salvos; use “Executar de novo” para ver outra vez.")
    else:
        texto = res.mensagem
    ficha = {"tipo": "executada" if res.ok else "falha_execucao", "estado": res.estado,
             "estado_nome": res.solicitacao.get_estado_display() if res.solicitacao else "",
             "objetivo": orquestrador.OBJETIVOS[s.objetivo].nome}
    from iativa.query.consolidado import talvez_consolidar

    exibido = talvez_consolidar(user, s, res)
    if exibido is not res:  # chat mestre: um bloco por setor (Etapa 6)
        texto += f" Setores: {len(exibido.blocos)}. {exibido.resumo}"
    msg = Mensagem.objects.create(conversa=c, papel=Papel.IA, texto=texto[:MAX_TEXTO], ficha=ficha,
                                  solicitacao=res.solicitacao)
    _tocar(c, "")
    return msg, exibido


@transaction.atomic
def executar(user, c: ConversationContext):
    """Executa a solicitação pronta desta conversa. Devolve (mensagem, resultado em memória)."""
    if not pode_escrever(user, c):
        raise PermissaoNegada("Somente leitura nesta conversa.")
    s = c.solicitacoes.filter(usuario=user, estado="pronta").order_by("-id").first()
    if s is None:
        raise ValidationError("Não há consulta pronta para executar.")
    Mensagem.objects.create(conversa=c, papel=Papel.USUARIO, autor=user, texto="Executar consulta.")
    return _executar_e_registrar(user, c, s)


@transaction.atomic
def executar_de_novo(user, c: ConversationContext, solicitacao_id):
    from iativa.query import servico

    if not pode_escrever(user, c):
        raise PermissaoNegada("Somente leitura nesta conversa.")
    anterior = c.solicitacoes.filter(pk=solicitacao_id, usuario=user).first()
    if anterior is None:
        raise PermissaoNegada("Solicitação indisponível.")
    Mensagem.objects.create(conversa=c, papel=Papel.USUARIO, autor=user, texto="Executar de novo.")
    nova = servico.repetir(user, anterior, conversa=c)
    r = orquestrador.avaliar(user, nova)
    if r.tipo != "pronta":
        return _resposta_ia(c, r), None
    nova.refresh_from_db()
    return _executar_e_registrar(user, c, nova)


def formulario_aprendizado(user, c: ConversationContext, alias: str) -> dict:
    from iativa.agent import aprendizado

    if not pode_escrever(user, c):
        raise PermissaoNegada("Somente leitura nesta conversa.")
    return aprendizado.campos_da_tabela(user, alias)


@transaction.atomic
def salvar_aprendizado(user, c: ConversationContext, alias: str, dados):
    from iativa.agent import aprendizado

    if not pode_escrever(user, c):
        raise PermissaoNegada("Somente leitura nesta conversa.")
    cm = aprendizado.salvar_proposta(user, alias, dados)
    Mensagem.objects.create(conversa=c, papel=Papel.USUARIO, autor=user, texto=f"Ensinar a consulta “{cm.nome}”.")
    _mensagem_modelo(c, cm, "Proposta criada. A consulta só passa a valer depois de aprovada por quem aprova definições.")
    _tocar(c, "")
    return cm


def _mensagem_modelo(c, cm, texto):
    ficha = {"tipo": "modelo", "modelo": cm.pk, "nome": cm.nome, "tabela": cm.tabela,
             "colunas": [x["rotulo"] for x in cm.spec["colunas"]], "campo_data": cm.spec.get("campo_data", ""),
             "filtros": cm.spec.get("filtros_fixos", []), "setores": [s.nome for s in cm.setores.all()],
             "situacao": cm.get_situacao_display()}
    return Mensagem.objects.create(conversa=c, papel=Papel.IA, texto=texto, ficha=ficha)


@transaction.atomic
def decidir_aprendizado(user, c: ConversationContext, modelo_id: int, aprovar: bool):
    from iativa.agent import aprendizado
    from iativa.agent.models import ConsultaModelo

    if not pode_ver(user, c):
        raise PermissaoNegada("Conversa indisponível.")
    cm = ConsultaModelo.objects.filter(pk=modelo_id).first()
    if cm is None or not c.mensagens.filter(ficha__modelo=modelo_id).exists():
        raise PermissaoNegada("Consulta indisponível.")
    cm = aprendizado.aprovar(user, cm) if aprovar else aprendizado.rejeitar(user, cm)
    texto = (f"Consulta “{cm.nome}” aprovada. Já pode perguntar por ela." if aprovar
             else f"Consulta “{cm.nome}” rejeitada.")
    _mensagem_modelo(c, cm, texto)
    _tocar(c, "")


def mensagens_com_interacao(user, c: ConversationContext) -> list[dict]:
    """Histórico para a tela. Só a última ficha de cada solicitação em andamento do próprio
    usuário fica editável; as anteriores aparecem como registro."""
    from iativa.agent.aprendizado import pode_aprender, pode_aprovar
    from iativa.agent.models import ConsultaModelo

    msgs = list(c.mensagens.select_related("autor", "solicitacao"))
    modelos = {cm.pk: cm for cm in ConsultaModelo.objects.filter(
        pk__in=[m.ficha.get("modelo") for m in msgs if m.ficha.get("modelo")])}
    ultima_modelo = {}
    for m in msgs:
        if m.ficha.get("modelo"):
            ultima_modelo[m.ficha["modelo"]] = m.pk
    administrador = pode_aprender(user)
    ultima_por_solicitacao = {}
    for m in msgs:
        if m.solicitacao_id and m.ficha:
            ultima_por_solicitacao[m.solicitacao_id] = m.pk
    out = []
    for m in msgs:
        s = m.solicitacao
        ultima_dono = bool(s and ultima_por_solicitacao.get(s.pk) == m.pk and s.usuario_id == user.pk)
        out.append({
            "m": m,
            "proprio": m.autor_id == user.pk,
            "editavel": ultima_dono and s.estado == "aguardando_parametros",
            "executavel": ultima_dono and s.estado == "pronta",
            "repetivel": ultima_dono and m.ficha.get("tipo") in ("executada", "falha_execucao"),
            "aprendivel": administrador and m.ficha.get("tipo") == "aprendizado" and m.pk == msgs[-1].pk,
            "ultima": m.pk == msgs[-1].pk,
            "aprovavel": bool(m.ficha.get("modelo") and ultima_modelo.get(m.ficha["modelo"]) == m.pk
                              and m.ficha["modelo"] in modelos and pode_aprovar(user, modelos[m.ficha["modelo"]])),
        })
    return out

