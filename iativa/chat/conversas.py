"""Conversas com histórico (M8): acesso, lateral e envio de mensagens.

Regras de acesso (todas no backend):
- individual e mestre: só o dono; o mestre exige "consultar" em mais de um setor;
- setor: lê quem tem "consultar" ou "ler memória" no setor; escreve quem é do setor e tem "consultar".

Guarda texto, fichas e snapshots de apresentação autorizados na M44. Nunca SQL.
"""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import OuterRef, Subquery
from django.db.models.functions import Coalesce
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
    if dias == 1:
        return "Ontem"
    if dias <= 7:
        return "Últimos 7 dias"
    nome = MESES[data.month - 1].capitalize()
    return nome if data.year == hoje.year else f"{nome} de {data.year}"


def lateral(user) -> dict:
    hoje = timezone.localdate()
    individuais = ConversationContext.objects.filter(
        tipo=T.INDIVIDUAL, usuario=user, encerrada_em__isnull=True, mensagens__isnull=False
    ).annotate(ultima_enviada=Subquery(Mensagem.objects.filter(conversa_id=OuterRef("pk"), papel=Papel.USUARIO)
        .order_by("-pk").values("criado_em")[:1]), estado_ativo=Subquery(RequestContext.objects.filter(conversa_id=OuterRef("pk"),
        estado__in=list(EM_PREPARACAO)).order_by("-id").values("estado")[:1])).distinct().order_by(
            Coalesce("ultima_enviada", "atualizado_em").desc(), "-pk")[:60]
    grupos: list[tuple[str, list]] = []
    for c in individuais:
        rotulo = _grupo(timezone.localdate(c.ultima_enviada or c.atualizado_em), hoje)
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


def excluir(user, conversa_id) -> None:
    """M71: só o dono exclui a própria conversa individual (mensagens, resultados e pedidos vão junto)."""
    ConversationContext.objects.get(pk=conversa_id, tipo=T.INDIVIDUAL, usuario=user).delete()


def _estado_lateral(c) -> str:
    if hasattr(c, "estado_ativo"):
        return "pronta" if c.estado_ativo == "pronta" else "pendente" if c.estado_ativo else ""
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
    c.atualizado_em = timezone.now()
    c.save(update_fields=["atualizado_em"])


def definir_titulo(c, sugerido, texto):
    """Usa a sugestão da triagem. Uma atualização condicional preserva renomeações em outra aba."""
    import re
    from iativa.agent.periodo import _norm

    if c.tipo != T.INDIVIDUAL or c.titulo:
        return
    titulo = " ".join(sugerido.split()).strip(' .?!“”"') if isinstance(sugerido, str) else ""
    if (not 3 <= len(titulo) <= 80 or not re.fullmatch(r"[\wÀ-ÿ ()-]+", titulo)
            or re.search(r"\d|_", titulo) or _norm(titulo) == _norm(texto).strip(" .?!")):
        assuntos = (
            (r"\bfatur\w*\b", "Consulta de faturamento"),
            (r"\binadimpl\w*\b", "Consulta de inadimplência"),
            (r"\bcontrat\w*\b", "Consulta de contratos"),
            (r"\b(?:admiss\w*|admit\w*|demit\w*|deslig\w*)\b", "Movimentações de pessoal"),
        )
        titulo = next((rotulo for padrao, rotulo in assuntos if re.search(padrao, _norm(texto))), "")
    if titulo and ConversationContext.objects.filter(pk=c.pk, titulo="").update(titulo=titulo):
        c.titulo = titulo


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
    # M94: parar vale na hora para a pessoa: a mensagem ainda sem resposta sai e o chat destrava, mesmo que a IA
    # esteja presa esperando o ERP (o que chegar depois é descartado em `enviar`).
    for m in Mensagem.objects.filter(pedido=pedido, autor=user, papel=Papel.USUARIO):
        if not m.conversa.mensagens.filter(papel=Papel.IA, pk__gt=m.pk).exists():
            m.delete()


def limpar_interrupcao(user, pedido) -> None:
    from .models import PedidoInterrompido

    if pedido:
        PedidoInterrompido.objects.filter(pedido=pedido, usuario=user).delete()


def enviar(user, c: ConversationContext, texto: str, provedor=None, pedido=None, *, origem=None):
    """`pedido`: id do envio gerado no navegador; se a pessoa clicar em "parar" antes da IA agir,
    `agente.Interrompido` é lançado e o envio é desfeito (inclusive a mensagem dela).

    M79: a mensagem da pessoa é gravada ANTES de a IA trabalhar (transação própria). Assim ela aparece ao
    recarregar a página ou voltar depois, junto do "trabalhando"; se o envio falhar ou for parado, ela sai.

    M22: o histórico guarda só `saida.texto` (intenção, critérios, estado). A resposta com números
    vai em `resultado.mensagem`, que existe só nesta resposta HTTP e nunca é gravada."""
    if not pode_escrever(user, c):
        raise PermissaoNegada("Somente leitura nesta conversa.")
    from iativa.agent import agente

    texto = _validar_texto(texto)
    with transaction.atomic():
        repetido = _envio_repetido(c, pedido)
        if repetido is not None:
            return repetido
        _ocupar(c)
        if envio_pendente(c) is not None:
            raise ValidationError(OCUPADA)
        m = Mensagem.objects.create(conversa=c, papel=Papel.USUARIO, autor=user, texto=texto, pedido=pedido,
                                    ficha={"mensagem_origem": origem.pk} if origem is not None else {})
    from iativa.agent import andamento

    try:
        with transaction.atomic():
            # Trava o chat durante a resposta (o worker e outra aba não entram); espera a 1ª etapa soltar.
            list(ConversationContext.objects.select_for_update(no_key=True).filter(pk=c.pk))
            with andamento.acompanhar(user.pk, pedido):
                from iativa.agent import prazo as _prazo, tempos
                with tempos.pedido() as t:
                    try:
                        contexto = {"contexto_id": origem.pk} if origem is not None else {}
                        saida = agente.conversar(user, c, texto, provedor=provedor, mensagem_usuario_id=m.pk,
                                                 interrompido=_verificador(user, pedido), **contexto)
                    except _prazo.PrazoEsgotado:
                        # M96: rede de segurança: tempo esgotado fora do ciclo (ex.: na triagem) não vira erro 500.
                        saida = agente.Saida(agente.SEM_TEMPO + " A IA demorou para responder. Tente de novo; se "
                                             "repetir, pergunte de forma mais direta.", ficha={"tipo": "falha_modelo"})
                saida.ficha = {**(saida.ficha or {}), "tempos_ms": dict(t)}  # M125: onde o prazo foi gasto
                if saida.resultado is not None and getattr(saida.resultado, "ok", True):
                    saida.ficha["mensagem_origem"] = origem.pk if origem is not None else m.pk
                parado = _verificador(user, pedido)
                if parado is not None and parado():
                    raise agente.Interrompido  # M94: parou enquanto esperava o ERP: a resposta é descartada
                andamento.avisar("Escrevendo a resposta")
            msg = Mensagem.objects.create(conversa=c, papel=Papel.IA, texto=saida.texto[:MAX_TEXTO],
                                          ficha=saida.ficha, solicitacao=saida.solicitacao)
            from .resultados import guardar
            guardar(msg, saida.resultado)
            definir_titulo(c, "", texto)  # fallback em fluxos que não chegam à triagem
            _tocar(c, texto)
    except BaseException:
        Mensagem.objects.filter(pk=m.pk).delete()  # parado ou falhou: a mensagem sai, como antes
        raise
    return msg, saida.resultado


ENVIO_VALIDO_MIN = 15  # depois disso, mensagem sem resposta não é mais "em andamento" (processo caiu)


def envio_pendente(c):
    """M79: a última mensagem é da pessoa, de um envio do navegador ainda sem resposta e recente: a IA está
    trabalhando nela (mesmo que a página tenha sido recarregada). Devolve a mensagem ou None."""
    from datetime import timedelta

    from django.utils import timezone
    ultima = c.mensagens.order_by("-criado_em", "-id").first()
    if (ultima is None or ultima.papel != Papel.USUARIO or ultima.pedido is None
            or ultima.criado_em < timezone.now() - timedelta(minutes=ENVIO_VALIDO_MIN)):
        return None
    return ultima


OCUPADA = ("Esta conversa ainda está trabalhando no pedido anterior. Aguarde a resposta ou abra um novo chat "
           "para perguntar outra coisa.")


def _envio_repetido(c, pedido):
    """M77: o mesmo envio (mesmo id do navegador) não roda duas vezes: devolve a resposta já dada."""
    if not pedido:
        return None
    original = Mensagem.objects.filter(conversa=c, pedido=pedido, papel=Papel.USUARIO).first()
    if original is None:
        return None
    resposta = (Mensagem.objects.filter(conversa=c, papel=Papel.IA, pk__gt=original.pk).order_by("pk").first())
    if resposta is None:
        raise ValidationError(OCUPADA)
    from .resultados import restaurar
    return resposta, restaurar(original.autor, resposta.resultado) if original.autor else None


def _ocupar(c):
    """M77: um pedido por chat. Trava a linha do chat até o fim desta resposta (o worker não pega pedido deste
    chat enquanto isso) e recusa se outro agente já trabalha nele. Chats diferentes seguem em paralelo."""
    from django.db import DatabaseError

    from iativa.agent.objetivos import conversas_ocupadas
    try:
        list(ConversationContext.objects.select_for_update(nowait=True, no_key=True).filter(pk=c.pk))
    except DatabaseError:
        raise ValidationError(OCUPADA) from None
    if c.pk in conversas_ocupadas():
        raise ValidationError(OCUPADA)


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
                 "Resultado salvo no histórico; use “Executar de novo” para atualizar os dados.")
    else:
        texto = res.mensagem
    ficha = {"tipo": "executada" if res.ok else "falha_execucao", "estado": res.estado,
             "estado_nome": res.solicitacao.get_estado_display() if res.solicitacao else "",
             "objetivo": orquestrador.OBJETIVOS[s.objetivo].nome}
    from iativa.query.consolidado import talvez_consolidar

    exibido = talvez_consolidar(user, s, res)
    if exibido is not res:  # chat mestre: um bloco por setor; o resumo com valores fica só na tela (M22)
        texto += f" Setores: {len(exibido.blocos)}."
    msg = Mensagem.objects.create(conversa=c, papel=Papel.IA, texto=texto[:MAX_TEXTO], ficha=ficha,
                                  solicitacao=res.solicitacao)
    from .resultados import guardar
    guardar(msg, exibido)
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
    from .resultados import restaurar
    from iativa.agent.models import ConsultaModelo

    msgs = list(c.mensagens.select_related("autor", "solicitacao"))
    from iativa.agent.models import Tarefa

    tarefas = {t.pk: t for t in Tarefa.objects.filter(
        usuario=user, pk__in=[m.ficha.get("tarefa") for m in msgs if m.ficha.get("tipo") == "tarefa"])}
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
    from .models import AvaliacaoResposta
    avaliacoes = {a.mensagem_id: a for a in AvaliacaoResposta.objects.filter(usuario=user, mensagem__in=msgs)}
    out = []
    anterior = None
    por_id = {m.pk: m for m in msgs if m.papel == Papel.USUARIO}
    for m in msgs:
        s = m.solicitacao
        ultima_dono = bool(s and ultima_por_solicitacao.get(s.pk) == m.pk and s.usuario_id == user.pk)
        if m.papel == Papel.USUARIO:
            anterior = m
        resultado = restaurar(user, m.resultado)
        origem = por_id.get(m.ficha.get("mensagem_origem"))
        if (origem is None and m.resultado and s and s.usuario_id == user.pk
                and (m.resultado.get("consolidado") or m.resultado.get("medida_calculada") == "comparacao")):
            origem = anterior
        out.append({
            "m": m,
            "linhas_resposta": _linhas_resposta(m, resultado),
            "resultado_salvo": resultado,
            "proprio": m.autor_id == user.pk,
            "editavel": ultima_dono and s.estado == "aguardando_parametros",
            "executavel": ultima_dono and s.estado == "pronta",
            "repetivel": ultima_dono and (resultado or m.ficha.get("tipo") == "falha_execucao"),
            "refazer": (origem.pk if m.papel == Papel.IA and resultado is not None and origem
                        and origem.autor_id == user.pk and origem.pk < m.pk else None),
            "aprendivel": administrador and m.ficha.get("tipo") == "aprendizado" and m.pk == msgs[-1].pk,
            "ultima": m.pk == msgs[-1].pk,
            "tarefa": tarefas.get(m.ficha.get("tarefa")) if m.ficha.get("tipo") == "tarefa" else None,
            "avaliavel": m.papel == Papel.IA and resultado is not None,
            "avaliacao": avaliacoes.get(m.pk),
            "aprovavel": bool(m.ficha.get("modelo") and ultima_modelo.get(m.ficha["modelo"]) == m.pk
                              and m.ficha["modelo"] in modelos and pode_aprovar(user, modelos[m.ficha["modelo"]])),
        })
    return out


def _linhas_resposta(m, resultado):
    import re

    texto = m.texto
    if resultado is not None:
        texto = re.sub(r"(?m)^Consulta executada (?:às|em) [^:\n]+(?::\d\d)*:\s*\d+ linha\(s\)\.[ \t]*", "", texto)
        texto = texto.replace('Resultado salvo no histórico; use “Executar de novo” para atualizar os dados.', '')
        texto = texto.removeprefix("Continuando o seu pedido, sem você precisar pedir de novo. ")
        texto = texto.strip()
    return texto.splitlines()


def refazer(user, c, resposta_id, pedido=None):
    if not pode_escrever(user, c):
        raise PermissaoNegada("Somente leitura nesta conversa.")
    item = next((i for i in mensagens_com_interacao(user, c) if i["m"].pk == resposta_id), None)
    if item is None or not item["refazer"]:
        raise PermissaoNegada("Esta resposta não está disponível para reexecução.")
    origem = c.mensagens.get(pk=item["refazer"], autor=user, papel=Papel.USUARIO)
    return enviar(user, c, origem.texto, pedido=pedido, origem=origem)



def avaliar(user, conversa_id, mensagem_id, correta: bool, comentario: str = ""):
    """M134: "Esta informação está correta?". Só quem vê a conversa avalia, e só respostas da IA com resultado.
    Uma avaliação por pessoa e resposta; votar de novo troca a anterior. "Não" fica para a TI verificar."""
    from .models import AvaliacaoResposta

    c = obter(user, conversa_id)
    m = c.mensagens.filter(pk=mensagem_id, papel=Papel.IA).first()
    if m is None or not m.resultado:
        raise ValidationError("Só respostas com resultado podem ser avaliadas.")
    a, _ = AvaliacaoResposta.objects.update_or_create(
        mensagem=m, usuario=user,
        defaults={"correta": bool(correta), "comentario": "" if correta else " ".join((comentario or "").split())[:1000],
                  "verificada": False})
    return a
