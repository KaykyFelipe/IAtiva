"""Orquestrador do agente (Etapa 4): interpretação → contrato → descoberta → esclarecimentos.

O modelo só interpreta. Quem decide é o backend:
- objetivo e parâmetros obrigatórios vêm do contrato (`contratos.py`);
- valores passam por conversão, validação de tipo, CPF/CNPJ e filiais autorizadas;
- tabelas do contrato precisam estar visíveis na descoberta de metadados;
- a liberação é `avaliar_completude` (determinística).

A mensagem do usuário é transitória: vai ao provedor e é descartada. Só ficam objetivo, estado
e valores de parâmetros (memória da solicitação), como nas Etapas 2 e 3.
"""
from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.utils import timezone

from iativa.accounts.authz import PermissaoNegada, filiais_autorizadas
from iativa.audit.servicos import registrar
from iativa.chat.estados import EM_PREPARACAO
from iativa.chat.models import EstadoSolicitacao as E, RequestContext
from iativa.chat.servicos import avaliar_completude, cancelar, criar_solicitacao, transicionar
from iativa.memory import servicos as memoria
from iativa.memory.models import Escopo, ParameterBinding, ParameterDefinition, Situacao, TipoValor
from iativa.memory.resolucao import CONFLITO, resolver
from iativa.metadata import descoberta

from .contratos import DOMINIO_APRENDIDO, OBJETIVOS, objetivos_para
from .conversao import SEPARADOR_PERIODO, converter, exibir
from .esquema import EntradaModelo, SaidaInvalida, validar
from .provedores import FalhaModelo, obter_provedor

DOMINIO = "financeiro_receber"
TENTATIVAS_MODELO = 2


@dataclass
class Pergunta:
    codigo: str
    nome: str
    descricao: str
    tipo: str
    opcoes: list = field(default_factory=list)
    conflito: bool = False


@dataclass
class Criterio:
    nome: str
    valor: str
    origem: str


@dataclass
class Resposta:
    tipo: str  # pergunta | pronta | bloqueada | cancelada | ajuda | fora_do_escopo | falha_modelo | sem_acesso
    mensagem: str
    solicitacao: RequestContext | None = None
    objetivo: str = ""
    perguntas: list[Pergunta] = field(default_factory=list)
    criterios: list[Criterio] = field(default_factory=list)
    ambiguidade: str = ""
    opcoes: list[str] = field(default_factory=list)
    erros: list[str] = field(default_factory=list)
    observacao: str = ""
    provedor: str = ""
    candidatos: list[dict] = field(default_factory=list)


ORIGENS = {
    Escopo.SOLICITACAO: "informado nesta solicitação",
    Escopo.USUARIO: "sua preferência salva",
    Escopo.SETOR: "padrão aprovado do setor",
    Escopo.EMPRESA: "padrão aprovado da empresa",
}


def _definicoes(codigos) -> dict[str, ParameterDefinition]:
    return {d.codigo: d for d in ParameterDefinition.objects.filter(codigo__in=codigos, ativo=True)}


def solicitacao_ativa(user, solicitacao_id) -> RequestContext | None:
    if not solicitacao_id:
        return None
    return RequestContext.objects.filter(
        pk=solicitacao_id, usuario=user, estado__in=list(EM_PREPARACAO)
    ).first()


def _catalogo(objetivos, defs: dict) -> list[dict]:
    out = []
    for o in objetivos:
        params = []
        for codigo in o.obrigatorios:
            d = defs.get(codigo)
            if d is not None:
                params.append({"codigo": codigo, "nome": d.nome, "descricao": d.descricao, "tipo": d.tipo,
                               "opcoes": list(d.opcoes or [])})
        out.append({"codigo": o.codigo, "nome": o.nome, "descricao": o.descricao, "parametros": params})
    return out


def _resumo(user, s: RequestContext | None, defs: dict) -> dict | None:
    if s is None:
        return None
    informados = {}
    for codigo in OBJETIVOS[s.objetivo].obrigatorios:
        d = defs.get(codigo)
        if d is None:
            continue
        r = resolver(d, user, s)
        if r.situacao == "resolvido":
            informados[codigo] = "[informado]" if d.contem_dado_pessoal else exibir(r.valor)
    return {"objetivo": s.objetivo, "estado": s.estado, "parametros_ja_definidos": informados}


def _interpretar(provedor, entrada, codigos_obj, codigos_par):
    ultima = ""
    for _ in range(TENTATIVAS_MODELO):
        try:
            return validar(provedor.interpretar(entrada), codigos_obj, codigos_par)
        except (FalhaModelo, SaidaInvalida) as exc:
            ultima = type(exc).__name__
    raise FalhaModelo(ultima)


def _registrar_parametro(user, s: RequestContext, definicao, texto: str):
    valor = converter(definicao, texto)
    if definicao.tipo == TipoValor.FILIAL and valor not in filiais_autorizadas(user, s.setor):
        raise ValidationError("Filial fora das suas filiais autorizadas.")
    anterior = (
        ParameterBinding.objects.filter(
            definicao=definicao, escopo=Escopo.SOLICITACAO, solicitacao=s, situacao=Situacao.APROVADA
        ).order_by("-id").first()
    )
    if anterior is not None:
        if anterior.valor == valor:
            return
        memoria.corrigir(user, anterior, valor)
    else:
        memoria.registrar_valor(user, definicao, Escopo.SOLICITACAO, valor, solicitacao=s)


def _aplicar_parametros(user, s, pares, defs) -> list[str]:
    erros = []
    obrigatorios = OBJETIVOS[s.objetivo].obrigatorios
    for codigo, texto in pares:
        d = defs.get(codigo)
        if codigo not in obrigatorios or d is None:
            continue  # parâmetro que o objetivo não usa é ignorado
        try:
            _registrar_parametro(user, s, d, texto)
        except ValidationError as exc:
            erros.append(f"{d.nome}: {exc.messages[0]}")
    return erros


def _para_aguardando(user, s) -> RequestContext:
    if s.estado in (E.RASCUNHO, E.PRONTA, E.BLOQUEADA, E.NAO_VALIDADA, E.FALHOU):
        s = transicionar(user, s, E.AGUARDANDO_PARAMETROS)
    return s


def _bloquear(user, s, motivo) -> RequestContext:
    s = _para_aguardando(user, s) if s.estado == E.PRONTA else s
    if s.estado != E.BLOQUEADA:
        s = transicionar(user, s, E.VALIDANDO)
        s = transicionar(user, s, E.BLOQUEADA)
    if motivo == "sem_acesso_entidade":
        registrar("acesso_negado", "solicitacao", usuario=user, setor=s.setor, entidade_id=s.pk,
                  estado_novo=motivo, resultado="negado")
    return s


def _verificar_entidades(user, objetivo) -> str | None:
    if objetivo.dominio == DOMINIO_APRENDIDO:
        return _verificar_tabela_aprendida(user, objetivo)
    try:
        visiveis = {t.alias for t in descoberta.entidades(user, objetivo.dominio)}
    except PermissaoNegada:
        return "sem_acesso_entidade"
    except Exception:  # rede, login, dicionário: sem detalhes para o usuário
        return "metadados_indisponiveis"
    if not set(objetivo.tabelas) <= visiveis:
        return "sem_acesso_entidade"
    return None


def _verificar_tabela_aprendida(user, objetivo) -> str | None:
    from iativa.metadata.catalogo import abrir_catalogo

    from .aprendizado import _restritas

    if set(objetivo.tabelas) & _restritas(user):
        return "sem_acesso_entidade"
    try:
        with abrir_catalogo() as cat:
            existentes = cat.tabelas(list(objetivo.tabelas))
    except Exception:
        return "metadados_indisponiveis"
    return None if set(objetivo.tabelas) <= set(existentes) else "metadados_indisponiveis"


def _oferecer_aprendizado(user, texto, atual, extra) -> "Resposta":
    from .aprendizado import candidatos

    try:
        lista = candidatos(user, texto)
    except Exception:
        return Resposta("fora_do_escopo", "Ainda não sei consultar isso e não consegui ler o dicionário agora.",
                        solicitacao=atual, **extra)
    if not lista:
        return Resposta("fora_do_escopo", "Ainda não sei consultar isso e não achei tabelas relacionadas no "
                        "dicionário. Tente usar outras palavras ou o nome da tabela (ex.: SRA).",
                        solicitacao=atual, **extra)
    return Resposta("aprendizado", "Ainda não sei consultar isso. Encontrei estas tabelas no dicionário do "
                    "Protheus. Escolha uma para eu aprender:", solicitacao=atual, candidatos=lista, **extra)


MOTIVOS = {
    "sem_acesso_entidade": "Você não tem acesso a todas as tabelas necessárias para esta consulta.",
    "metadados_indisponiveis": "Não foi possível consultar a estrutura do ERP agora. Tente de novo em instantes.",
    "regra_desconhecida": "Esta consulta depende de uma definição que ainda não foi cadastrada.",
    "sem_autorizacao": "Sem permissão de consulta ou sem filiais autorizadas neste setor.",
}


def avaliar(user, s: RequestContext, erros=None) -> Resposta:
    """Checagens do backend e próxima pergunta. Não chama o modelo."""
    objetivo = OBJETIVOS[s.objetivo]
    defs = _definicoes(objetivo.obrigatorios)
    base = {"solicitacao": s, "objetivo": objetivo.nome, "erros": list(erros or [])}

    if len(defs) != len(objetivo.obrigatorios):
        s = _bloquear(user, s, "regra_desconhecida")
        return Resposta("bloqueada", MOTIVOS["regra_desconhecida"], **{**base, "solicitacao": s})
    motivo = _verificar_entidades(user, objetivo)
    if motivo:
        s = _bloquear(user, s, motivo)
        return Resposta("bloqueada", MOTIVOS[motivo], **{**base, "solicitacao": s})

    if s.estado == E.PRONTA:
        s = _para_aguardando(user, s)
    avaliacao = avaliar_completude(user, s, [defs[c] for c in objetivo.obrigatorios])
    s.refresh_from_db()
    base["solicitacao"] = s
    if avaliacao.estado == E.BLOQUEADA:
        return Resposta("bloqueada", MOTIVOS["sem_autorizacao"], **base)
    criterios = [
        Criterio(defs[r.definicao].nome, exibir(r.valor), ORIGENS.get(r.escopo, r.escopo))
        for r in avaliacao.resolucoes if r.situacao == "resolvido"
    ]
    if avaliacao.estado == E.PRONTA:
        return Resposta(
            "pronta",
            "Todos os critérios estão definidos. Confira e clique em Executar consulta.",
            criterios=criterios, **base,
        )
    perguntas = []
    for r in avaliacao.pendencias:
        d = defs[r.definicao]
        opcoes = list(d.opcoes or [])
        if d.tipo == TipoValor.FILIAL:
            opcoes = sorted(filiais_autorizadas(user, s.setor))
        perguntas.append(Pergunta(d.codigo, d.nome, d.descricao, d.tipo, opcoes, r.situacao == CONFLITO))
    return Resposta("pergunta", "Preciso de mais informações antes de consultar:", perguntas=perguntas,
                    criterios=criterios, **base)


def processar_mensagem(user, texto: str, solicitacao_id=None, provedor=None, conversa=None) -> Resposta:
    from .aprendizado import pode_aprender

    objetivos = objetivos_para(user)
    administrador = pode_aprender(user)
    if not objetivos and not administrador:
        registrar("acesso_negado", "agente", usuario=user, estado_novo="dominio", resultado="negado")
        return Resposta("sem_acesso", "Seu usuário ainda não tem acesso a consultas. Fale com o administrador.")

    atual = solicitacao_ativa(user, solicitacao_id)
    todos_codigos = sorted({c for o in objetivos for c in o.obrigatorios} | {"filial-analise"})
    defs = _definicoes(todos_codigos)
    try:
        trechos = [
            {"documento": t.titulo_documento, "secao": t.secao, "texto": t.texto}
            for t in descoberta.conhecimento(user, DOMINIO, texto, limite=3)
        ]
    except Exception:
        trechos = []
    entrada = EntradaModelo(
        mensagem=texto, hoje=timezone.localdate().isoformat(), objetivos=_catalogo(objetivos, defs),
        solicitacao_atual=_resumo(user, atual, defs), trechos=trechos,
    )
    provedor = provedor or obter_provedor()
    codigos_obj = [o.codigo for o in objetivos]
    try:
        inter = _interpretar(provedor, entrada, codigos_obj, todos_codigos)
    except FalhaModelo:
        registrar("modelo_falhou", "agente", usuario=user, estado_novo=provedor.nome, resultado="erro")
        return Resposta("falha_modelo", "Não consegui interpretar a mensagem agora. Tente reformular ou repetir.",
                        solicitacao=atual, provedor=provedor.nome)
    registrar("interpretacao", "agente", usuario=user, estado_novo=inter.intencao,
              entidade_id=atual.pk if atual else None, linhas=len(inter.parametros))
    extra = {"observacao": inter.observacao, "provedor": provedor.nome}

    if inter.intencao == "fora_do_escopo" and administrador:
        return _oferecer_aprendizado(user, texto, atual, extra)
    if inter.intencao in ("ajuda", "fora_do_escopo"):
        nomes = "; ".join(o.nome for o in objetivos) or "nenhuma consulta liberada ainda"
        msg = "Posso ajudar com: " + nomes + "."
        if inter.intencao == "fora_do_escopo":
            msg = "Essa pergunta está fora do que consigo responder por enquanto. " + msg
        return Resposta(inter.intencao, msg, solicitacao=atual, **extra)

    if inter.intencao == "cancelar":
        if atual is None:
            return Resposta("ajuda", "Não há solicitação em andamento para cancelar.", **extra)
        atual = cancelar(user, atual)
        return Resposta("cancelada", "Solicitação cancelada.", solicitacao=atual, **extra)

    objetivo = inter.objetivo or (atual.objetivo if atual else "")
    if not objetivo:
        nomes = "; ".join(o.nome for o in objetivos)
        return Resposta("ajuda", "Qual destas informações você procura? " + nomes + ".", **extra)

    if atual is None or atual.objetivo != objetivo:
        if atual is not None:
            cancelar(user, atual)  # mudança de assunto: a anterior é encerrada
        atual = criar_solicitacao(user, OBJETIVOS[objetivo].dominio, objetivo,
                                  conversa=conversa or (atual.conversa if atual else None))

    erros = _aplicar_parametros(user, atual, inter.parametros, defs)
    atual.refresh_from_db()
    if inter.pergunta:
        atual = _para_aguardando(user, atual)
        return Resposta("pergunta", "Preciso esclarecer um ponto antes de seguir:", solicitacao=atual,
                        objetivo=OBJETIVOS[objetivo].nome, ambiguidade=inter.pergunta,
                        opcoes=list(inter.opcoes), erros=erros, **extra)
    resposta = avaliar(user, atual, erros)
    resposta.observacao, resposta.provedor = inter.observacao, provedor.nome
    return resposta


def responder_parametro(user, solicitacao_id, codigo: str, valor: str) -> Resposta:
    """Resposta direta a uma pergunta pendente (formulário), sem passar pelo modelo."""
    s = solicitacao_ativa(user, solicitacao_id)
    if s is None:
        return Resposta("ajuda", "Não há solicitação em andamento.")
    if codigo not in OBJETIVOS[s.objetivo].obrigatorios:
        return Resposta("ajuda", "Parâmetro não pertence a esta solicitação.", solicitacao=s)
    defs = _definicoes([codigo])
    erros = _aplicar_parametros(user, s, [(codigo, valor)], defs)
    s.refresh_from_db()
    return avaliar(user, s, erros)


def valor_periodo(inicio: str, fim: str) -> str:
    return f"{inicio}{SEPARADOR_PERIODO}{fim}"
