"""Chat com histórico (M8): conversas individuais, do setor e mestre.

O acesso é sempre conferido em `conversas.py`. O histórico guarda texto e fichas de critérios,
nunca resultados de consultas.
"""
import uuid

from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from iativa.accounts.authz import PermissaoNegada, setor_do_usuario
from iativa.agent.agente import Interrompido
from iativa.memory.models import ParameterDefinition, TipoValor

from . import conversas
from .estados import TransicaoInvalida
from .forms import MessageForm
from .models import ConversationContext, TipoConversa

FRASES_INICIO = (
    "O que vamos descobrir hoje?", "Como posso ajudar você hoje?",
    "O que você quer consultar agora?", "Qual informação você precisa encontrar?",
    "Por onde vamos começar hoje?", "Vamos olhar os dados juntos?",
)


def _pedido(request):
    """Id do envio gerado no navegador (para o botão "parar"); qualquer coisa fora do formato é ignorada."""
    try:
        return uuid.UUID(request.POST.get("pedido", ""))
    except ValueError:
        return None


def _interrompido():
    return HttpResponse(status=204)  # o navegador já desfez a tela; nada a trocar


def _obter(request, conversa_id) -> ConversationContext:
    try:
        return conversas.obter(request.user, conversa_id)
    except PermissaoNegada:
        raise Http404 from None


def _contexto(request, c=None, form=None, erro="", resultado=None, resultado_msg=None, aprendizado=None,
              enviado=None):
    setor = setor_do_usuario(request.user)
    nome = request.user.get_full_name() or request.user.get_username()
    iniciais = "".join(p[0] for p in nome.split()[:2]).upper()
    return {
        "lateral": conversas.lateral(request.user),
        "conversa": c,
        "mensagens": conversas.mensagens_com_interacao(request.user, c) if c else [],
        "pode_escrever": conversas.pode_escrever(request.user, c) if c else bool(setor),
        "form": form or MessageForm(),
        "erro": erro,
        # Resultado só existe nesta resposta (memória); não vai para o banco nem para a sessão.
        "resultado": resultado,
        "resultado_msg": resultado_msg,
        "aprendizado": aprendizado,
        "enviado": enviado or _enviado_aprendizado(),
        "tipos": TipoValor,
        "TipoConversa": TipoConversa,
        "perfil": {"nome": nome, "primeiro_nome": (request.user.first_name.strip() or request.user.get_username()).split()[0],
                   "iniciais": iniciais or "?", "setor": setor.nome if setor else "Sem setor"},
        "layout": _layout(request),
        "tarefa_fixa": tarefa_ativa(request.user, c),
        "frase_inicio": FRASES_INICIO[request.session.get("inicio_frase", 0) % len(FRASES_INICIO)],
    }


def _render(request, c=None, **extra):
    ctx = _contexto(request, c, **extra)
    htmx = request.headers.get("HX-Request") == "true"
    ctx["oob_lateral"] = htmx  # atualiza também a lateral (título e estado das conversas)
    template = "chat/_conversa.html" if htmx else "chat/index.html"
    return render(request, template, ctx)


@require_GET
def index(request):
    # Só o índice de apresentação vai à sessão; nenhuma mensagem ou resultado.
    request.session["inicio_frase"] = (request.session.get("inicio_frase", -1) + 1) % len(FRASES_INICIO)
    return _render(request)


@require_GET
def conversa(request, conversa_id):
    return _render(request, _obter(request, conversa_id))


def _layout(request):
    l = request.session.get("layout", {})
    largura = l.get("largura", 278)
    return {"largura": largura if isinstance(largura, int) and 224 <= largura <= 430 else 278,
            "ampla": bool(l.get("ampla")), "recolhida": bool(l.get("recolhida"))}


@require_POST
def layout(request):
    """Lembra só o layout da tela (largura da lateral, leitura expandida, lateral recolhida)."""
    try:
        largura = max(224, min(430, int(request.POST.get("largura", 278))))
    except ValueError:
        largura = 278
    request.session["layout"] = {"largura": largura, "ampla": bool(request.POST.get("ampla")),
                                 "recolhida": bool(request.POST.get("recolhida"))}
    return HttpResponse(status=204)


@require_POST
def renomear(request, conversa_id):
    try:
        c = conversas.renomear(request.user, conversa_id, request.POST.get("titulo", ""))
    except ConversationContext.DoesNotExist:
        raise Http404 from None
    except ValidationError as exc:
        return HttpResponse(exc.messages[0], status=400)
    return HttpResponse(c.titulo)


@require_POST
def nova(request):
    """Primeira mensagem de uma conversa nova: cria a conversa e envia."""
    form = MessageForm(request.POST)
    if not form.is_valid():
        return _render(request, form=form)
    try:
        conversas._validar_texto(form.cleaned_data["mensagem"])
        c = conversas.nova_individual(request.user)
        msg, resultado = conversas.enviar(request.user, c, form.cleaned_data["mensagem"], pedido=_pedido(request))
    except Interrompido:
        conversas.limpar_interrupcao(request.user, _pedido(request))
        return _interrompido()
    except PermissaoNegada:
        return _render(request, erro="Seu usuário ainda não tem setor. Fale com o administrador.")
    except ValidationError as exc:
        return _render(request, form=form, erro=exc.messages[0])
    resposta = _render(request, c, resultado=resultado, resultado_msg=msg.pk)
    resposta["HX-Push-Url"] = reverse("chat:conversa", args=[c.pk])  # endereço da conversa nova
    return resposta


def _ir(request, c):
    url = reverse("chat:conversa", args=[c.pk])
    if request.headers.get("HX-Request") == "true":
        resposta = HttpResponse(status=204)
        resposta["HX-Redirect"] = url  # recarrega a página para a lateral mostrar a conversa nova
        return resposta
    return redirect(url)


@require_POST
def mensagem(request, conversa_id):
    c = _obter(request, conversa_id)
    form = MessageForm(request.POST)
    if not form.is_valid():
        return _render(request, c, form=form)
    try:
        msg, resultado = conversas.enviar(request.user, c, form.cleaned_data["mensagem"], pedido=_pedido(request))
    except Interrompido:
        conversas.limpar_interrupcao(request.user, _pedido(request))
        return _interrompido()
    except PermissaoNegada:
        return _render(request, c, erro="Esta conversa é somente leitura para você.")
    except ValidationError as exc:
        return _render(request, c, form=form, erro=exc.messages[0])
    return _render(request, c, resultado=resultado, resultado_msg=msg.pk)


@require_POST
def parametro(request, conversa_id):
    c = _obter(request, conversa_id)
    codigo = request.POST.get("codigo", "")[:60]
    if "inicio" in request.POST or "fim" in request.POST:
        valor = f"{request.POST.get('inicio', '')}..{request.POST.get('fim', '')}"
    else:
        valor = request.POST.get("valor", "")[:200]
    d = ParameterDefinition.objects.filter(codigo=codigo).first()
    try:
        conversas.responder(request.user, c, codigo, d.nome if d else codigo, valor)
    except (PermissaoNegada, ValidationError) as exc:
        return _render(request, c, erro=getattr(exc, "messages", ["Não foi possível registrar."])[0])
    return _render(request, c)


@require_POST
def executar(request, conversa_id):
    c = _obter(request, conversa_id)
    try:
        msg, resultado = conversas.executar(request.user, c)
    except (PermissaoNegada, ValidationError, TransicaoInvalida) as exc:
        return _render(request, c, erro=getattr(exc, "messages", ["Não foi possível executar."])[0])
    return _render(request, c, resultado=resultado, resultado_msg=msg.pk)


@require_POST
def executar_de_novo(request, conversa_id, solicitacao_id):
    c = _obter(request, conversa_id)
    try:
        msg, resultado = conversas.executar_de_novo(request.user, c, solicitacao_id)
    except (PermissaoNegada, ValidationError, TransicaoInvalida) as exc:
        return _render(request, c, erro=getattr(exc, "messages", ["Não foi possível executar."])[0])
    return _render(request, c, resultado=resultado, resultado_msg=msg.pk)


@require_POST
def aprender(request, conversa_id):
    """Abre o formulário de aprendizado para a tabela escolhida (M9). O formulário é transitório."""
    c = _obter(request, conversa_id)
    alias = request.POST.get("tabela", "")[:3].upper()
    try:
        info = conversas.formulario_aprendizado(request.user, c, alias)
    except (PermissaoNegada, ValidationError) as exc:
        return _render(request, c, erro=getattr(exc, "messages", ["Não foi possível abrir esta tabela."])[0])
    except Exception:
        return _render(request, c, erro="Não foi possível ler o dicionário agora. Tente de novo.")
    return _render(request, c, aprendizado=info)


def _enviado_aprendizado(post=None) -> dict:
    """Valores já preenchidos no formulário de aprendizado, para não perdê-los quando o salvar falha."""
    post = post or {}
    lista = post.getlist if hasattr(post, "getlist") else (lambda k: [])
    return {
        "nome": post.get("nome", ""), "descricao": post.get("descricao", ""),
        "campo_data": post.get("campo_data", ""), "campo_busca": post.get("campo_busca", ""),
        "filtraveis": lista("filtraveis"), "colunas": lista("colunas"), "setores": lista("setores"),
        "filtros": [{"n": n, "campo": post.get(f"filtro_campo_{n}", ""), "valor": post.get(f"filtro_valor_{n}", "")}
                    for n in range(3)],
    }


@require_POST
def aprender_salvar(request, conversa_id):
    c = _obter(request, conversa_id)
    alias = request.POST.get("tabela", "")[:3].upper()
    try:
        conversas.salvar_aprendizado(request.user, c, alias, request.POST)
    except (PermissaoNegada, ValidationError) as exc:
        erro = getattr(exc, "messages", ["Não foi possível salvar."])[0]
        try:
            info = conversas.formulario_aprendizado(request.user, c, alias)
        except Exception:
            info = None
        return _render(request, c, erro=erro, aprendizado=info, enviado=_enviado_aprendizado(request.POST))
    return _render(request, c)


@require_POST
def aprender_decidir(request, conversa_id, modelo_id):
    c = _obter(request, conversa_id)
    try:
        conversas.decidir_aprendizado(request.user, c, modelo_id, request.POST.get("decisao") == "aprovar")
    except (PermissaoNegada, ValidationError) as exc:
        return _render(request, c, erro=getattr(exc, "messages", ["Não foi possível registrar."])[0])
    return _render(request, c)


@require_POST
def cancelar(request, conversa_id):
    c = _obter(request, conversa_id)
    conversas.cancelar_atual(request.user, c)
    return _render(request, c)


@require_GET
def setor(request, setor_id):
    try:
        c = conversas.do_setor(request.user, setor_id)
    except PermissaoNegada:
        raise Http404 from None
    return redirect("chat:conversa", conversa_id=c.pk)


@require_GET
def mestre(request):
    try:
        c = conversas.mestre(request.user)
    except PermissaoNegada:
        raise Http404 from None
    return redirect("chat:conversa", conversa_id=c.pk)


def _render_memoria(request, setor_id=None, aviso="", erro=""):
    from iativa.agent import memoria

    setores = list(memoria.setores_visiveis(request.user))
    escolhido = next((s for s in setores if str(s.pk) == str(setor_id)), None)
    if escolhido is None:
        proprio = setor_do_usuario(request.user)
        escolhido = next((s for s in setores if proprio and s.pk == proprio.pk), setores[0] if setores else None)
    ctx = _contexto(request)
    ctx.update({
        "pagina_memoria": True,
        "setores_memoria": setores,
        "memoria": memoria.memoria_do_setor(request.user, escolhido) if escolhido else None,
        "pendencias": memoria.pendencias(request.user),
        "aviso": aviso,
        "erro": erro,
    })
    return render(request, "chat/index.html", ctx)


@require_GET
def memoria(request):
    """Etapa 6: o que vale em cada setor do usuário e a fila de aprovação."""
    return _render_memoria(request, request.GET.get("setor"))


@require_POST
def memoria_decidir(request):
    from iativa.agent import memoria as mem

    ref, acao = request.POST.get("ref", ""), request.POST.get("acao", "")
    if acao not in ("aprovar", "rejeitar") or ref not in {p.ref for p in mem.pendencias(request.user)}:
        return _render_memoria(request, request.POST.get("setor"), erro="Proposta indisponível.")
    try:
        descricao = mem.decidir(request.user, ref, acao == "aprovar")
    except (PermissaoNegada, ValidationError) as exc:
        msg = getattr(exc, "messages", None)
        return _render_memoria(request, request.POST.get("setor"), erro=msg[0] if msg else str(exc))
    feito = "aprovada" if acao == "aprovar" else "rejeitada"
    return _render_memoria(request, request.POST.get("setor"), aviso=f"Proposta “{descricao}” {feito}.")


@require_POST
def parar(request):
    """Botão "parar" do compositor: a resposta em andamento é descartada se a IA ainda não agiu."""
    pedido = _pedido(request)
    if pedido is None:
        return HttpResponse(status=400)
    conversas.interromper(request.user, pedido)
    return HttpResponse(status=204)


# --- tarefas longas (M25) -------------------------------------------------------------------------

def _tarefa(request, tarefa_id):
    from iativa.agent.models import Tarefa

    t = Tarefa.objects.select_related("conversa").filter(pk=tarefa_id, usuario=request.user).first()
    if t is None or not conversas.pode_ver(request.user, t.conversa):
        raise Http404
    return t


@require_GET
def tarefa(request, tarefa_id):
    """Estado da tarefa (persistido, sem dados do ERP). O navegador consulta a cada poucos segundos."""
    t = _tarefa(request, tarefa_id)
    return render(request, "chat/_tarefa.html", {"tarefa": t, "acompanhando": request.GET.get("acompanhando") == "1"})


@require_POST
def tarefa_cancelar(request, tarefa_id):
    from iativa.agent import tarefas

    t = tarefas.cancelar(request.user, _tarefa(request, tarefa_id).pk)
    return render(request, "chat/_tarefa.html", {"tarefa": t})


@require_GET
def tarefa_conclusao(request, tarefa_id):
    """Conclusão com os dados: refeita agora, com as permissões de agora, e nunca gravada (R3)."""
    from iativa.agent import investigacao, tarefas

    t = _tarefa(request, tarefa_id)
    if t.situacao not in ("concluida", "limite"):
        return HttpResponse("A verificação ainda não terminou.", status=409)
    try:
        conclusao = tarefas.conclusao(request.user, t)
    except investigacao.SemPermissao as exc:
        return render(request, "chat/_conclusao.html", {"tarefa": t, "erro": str(exc)})
    except Exception as exc:  # sem detalhes: podem conter dados
        return render(request, "chat/_conclusao.html",
                      {"tarefa": t, "erro": f"Não consegui consultar de novo agora ({type(exc).__name__})."})
    return render(request, "chat/_conclusao.html", {"tarefa": t, "c": conclusao})


@require_GET
def andamento(request, pedido):
    """O que a IA está fazendo agora neste envio (M26). Só textos operacionais, só do próprio usuário."""
    from django.http import JsonResponse

    from iativa.agent import andamento as ao_vivo

    resposta = JsonResponse(ao_vivo.ler(request.user.pk, pedido))
    resposta["Cache-Control"] = "no-store"
    return resposta


def tarefa_ativa(user, c):
    """Tarefa em andamento da conversa (barra fixa e botão de parar). Terminou: a barra some."""
    from iativa.agent.models import TERMINAIS, Tarefa

    if c is None:
        return None
    return Tarefa.objects.filter(usuario=user, conversa=c).exclude(situacao__in=TERMINAIS).order_by("-id").first()


@require_GET
def atividade(request, conversa_id):
    """Barra fixa acima do compositor com o andamento da tarefa em segundo plano (M26)."""
    c = _obter(request, conversa_id)
    return render(request, "chat/_atividade.html", {"conversa": c, "tarefa_fixa": tarefa_ativa(request.user, c)})
