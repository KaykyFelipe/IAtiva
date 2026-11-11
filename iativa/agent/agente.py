"""Agente conversacional (M10).

A cada mensagem: monta o contexto (histórico curto, objetivos permitidos, estado da solicitação,
memória do agente, conhecimento aprovado) → o modelo responde em texto e escolhe UMA ação →
o backend autoriza e executa a ação → a resposta vira mensagem da conversa.

Regras que continuam valendo: lista fechada de objetivos, parâmetros validados no backend,
filiais autorizadas, compilador + validador AST, resultado só em memória (nunca no histórico
nem no modelo), auditoria só com códigos.
"""
import re
import unicodedata
from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.utils import timezone

from iativa.accounts.authz import Permissao, filiais_autorizadas, setor_do_usuario, setores_autorizados
from iativa.audit.servicos import registrar
from iativa.chat.estados import EM_PREPARACAO
from iativa.chat.models import EstadoSolicitacao as E
from iativa.chat.servicos import cancelar, criar_solicitacao
from iativa.memory.models import TipoValor
from iativa.memory.resolucao import RESOLVIDO, resolver
from iativa.memory.validadores import contem_cpf_cnpj

from . import orquestrador as orq
from .aprendizado import candidatos, pode_aprender
from .contratos import OBJETIVOS, objetivos_para
from .conversao import exibir
from .decisao import DecisaoInvalida, EntradaAgente, validar
from .models import ConsultaModelo, EscopoNota, NotaAgente, SituacaoModelo
from .provedores import FalhaModelo, obter_provedor

HISTORICO = 12
TENTATIVAS = 2


@dataclass
class Saida:
    texto: str
    ficha: dict = field(default_factory=dict)
    solicitacao: object = None
    resultado: object = None  # só em memória


def _norm(t: str) -> str:
    return unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower().strip()


def _ativa(user, c):
    return c.solicitacoes.filter(usuario=user, estado__in=list(EM_PREPARACAO)).order_by("-id").first()


def _filiais(user) -> list[str]:
    out = set()
    for sid in setores_autorizados(user, Permissao.CONSULTAR):
        from iativa.accounts.models import Department

        out |= filiais_autorizadas(user, Department.objects.get(pk=sid))
    return sorted(out)


def _estado(user, s, defs) -> dict | None:
    if s is None:
        return None
    obj = OBJETIVOS[s.objetivo]
    definidos, faltando = {}, []
    for codigo in obj.obrigatorios:
        d = defs.get(codigo)
        if d is None:
            continue
        r = resolver(d, user, s)
        if r.situacao == RESOLVIDO:
            definidos[d.nome] = "[informado]" if d.contem_dado_pessoal else exibir(r.valor)
        else:
            faltando.append(d.nome)
    return {"objetivo": s.objetivo, "nome": obj.nome, "estado": s.get_estado_display(),
            "ja_definido": definidos, "faltando": faltando, "completa": not faltando}


def _historico(c, ate=None) -> list[dict]:
    msgs = list(c.mensagens.order_by("-criado_em", "-id")[:HISTORICO + 1])[::-1]
    if ate is not None:
        msgs = [m for m in msgs if m.pk != ate]
    return [{"quem": "usuario" if m.papel == "usuario" else "iativa", "texto": m.texto[:600]} for m in msgs[-HISTORICO:]]


def _notas(user) -> list[str]:
    from django.db.models import Q

    qs = NotaAgente.objects.filter(ativo=True).filter(Q(escopo=EscopoNota.EMPRESA) | Q(usuario=user))
    return [n.texto for n in qs[:40]]


def _catalogo(user, objetivos, defs) -> list[dict]:
    cat = orq._catalogo(objetivos, defs)
    filiais = _filiais(user)
    for o in cat:
        for p in o["parametros"]:
            if p["tipo"] == TipoValor.FILIAL:
                p["opcoes"] = filiais
    return cat


def _interpretar(provedor, entrada, codigos_obj, codigos_par):
    for _ in range(TENTATIVAS):
        try:
            return validar(provedor.decidir(entrada), codigos_obj, codigos_par)
        except (FalhaModelo, DecisaoInvalida):
            continue
    raise FalhaModelo("sem decisão válida")


def conversar(user, c, texto: str, provedor=None, mensagem_usuario_id=None) -> Saida:
    objetivos = objetivos_para(user)
    administrador = pode_aprender(user)
    if not objetivos and not administrador:
        registrar("acesso_negado", "agente", usuario=user, estado_novo="dominio", resultado="negado")
        return Saida("Seu usuário ainda não tem acesso a consultas. Fale com o administrador.")

    codigos_par = sorted({cod for o in objetivos for cod in o.obrigatorios} | {"filial-analise"})
    defs = orq._definicoes(codigos_par)
    atual = _ativa(user, c)
    try:
        trechos = [{"documento": t.titulo_documento, "secao": t.secao, "texto": t.texto}
                   for t in orq.descoberta.conhecimento(user, orq.DOMINIO, texto, limite=3)]
    except Exception:
        trechos = []
    aprendidos = []
    if administrador:
        aprendidos = [{"codigo": cm.codigo, "nome": cm.nome, "tabela": cm.tabela, "situacao": cm.get_situacao_display()}
                      for cm in ConsultaModelo.objects.exclude(situacao=SituacaoModelo.REMOVIDA)]
    entrada = EntradaAgente(
        mensagem=texto, hoje=timezone.localdate().isoformat(), historico=_historico(c, mensagem_usuario_id),
        objetivos=_catalogo(user, objetivos, defs), solicitacao_atual=_estado(user, atual, defs),
        notas=_notas(user), aprendidos=aprendidos, trechos=trechos, administrador=administrador,
    )
    provedor = provedor or obter_provedor()
    try:
        d = _interpretar(provedor, entrada, [o.codigo for o in objetivos], codigos_par)
    except FalhaModelo:
        registrar("modelo_falhou", "agente", usuario=user, estado_novo=provedor.nome, resultado="erro")
        return Saida("Não consegui processar sua mensagem agora. Pode repetir ou escrever de outro jeito?",
                     ficha={"tipo": "falha_modelo"})
    registrar("interpretacao", "agente", usuario=user, estado_novo=d.acao, entidade_id=atual.pk if atual else None,
              linhas=len(d.parametros))
    ficha = {"provedor": provedor.nome, "opcoes": list(d.opcoes)}

    acao = d.acao
    if acao in ("aprender_tabela", "remover_aprendizado") and not administrador:
        return Saida("Essa ação é só para administradores.", ficha)
    if acao == "preparar_consulta":
        return _preparar(user, c, d, atual, defs, ficha)
    if acao == "executar_consulta":
        if atual is None:
            return Saida(d.resposta or "Não há consulta em andamento. O que você quer consultar?", ficha)
        return _avaliar_e_executar(user, atual, d.resposta, [], ficha)
    if acao == "cancelar_consulta":
        if atual is not None:
            cancelar(user, atual)
        return Saida(d.resposta or ("Cancelei a consulta." if atual else "Não havia consulta em andamento."), ficha)
    if acao == "aprender_tabela":
        return _aprender(user, d, texto, ficha)
    if acao == "listar_aprendizados":
        return _listar(administrador, objetivos, d, ficha)
    if acao == "remover_aprendizado":
        return _remover(user, d, ficha)
    if acao == "lembrar":
        return _lembrar(user, d, administrador, ficha)
    return Saida(d.resposta or _ajuda(objetivos), ficha)


def _ajuda(objetivos) -> str:
    if not objetivos:
        return "Ainda não há consultas liberadas para você. Posso aprender uma nova se você me disser o que procura."
    return "Posso consultar: " + "; ".join(o.nome for o in objetivos) + ". O que você precisa?"


def _preparar(user, c, d, atual, defs, ficha) -> Saida:
    objetivo = d.objetivo or (atual.objetivo if atual else "")
    if not objetivo:
        return Saida(d.resposta or "Que informação você procura?", ficha)
    if atual is not None and atual.objetivo != objetivo:
        cancelar(user, atual)  # novo assunto: a anterior é encerrada sem precisar clicar
        atual = None
    if atual is None:
        atual = criar_solicitacao(user, OBJETIVOS[objetivo].dominio, objetivo, conversa=c)
    erros = orq._aplicar_parametros(user, atual, d.parametros, defs)
    atual.refresh_from_db()
    return _avaliar_e_executar(user, atual, d.resposta, erros, ficha)


def _frase_pendencias(r) -> str:
    partes = []
    for p in r.perguntas:
        extra = ""
        if p.opcoes:
            extra = " (" + ", ".join(p.opcoes[:6]) + ")"
        elif p.tipo == TipoValor.DATA:
            extra = " (DD/MM/AAAA)"
        elif p.tipo == TipoValor.PERIODO:
            extra = " (de DD/MM/AAAA a DD/MM/AAAA)"
        partes.append(p.nome.lower() + extra)
    if not partes:
        return ""
    lista = partes[0] if len(partes) == 1 else ", ".join(partes[:-1]) + " e " + partes[-1]
    return f"Para consultar {r.objetivo.lower()}, me diga: {lista}."


def _avaliar_e_executar(user, s, resposta_modelo, erros, ficha) -> Saida:
    r = orq.avaliar(user, s, erros)
    s.refresh_from_db()
    criterios = [{"nome": x.nome, "valor": x.valor, "origem": x.origem} for x in r.criterios]
    ficha = {**ficha, "objetivo": r.objetivo, "criterios": criterios, "estado": s.estado}
    texto_erros = " ".join(r.erros)
    if r.tipo == "pergunta":
        if not ficha["opcoes"] and len(r.perguntas) == 1 and r.perguntas[0].opcoes:
            ficha["opcoes"] = r.perguntas[0].opcoes[:6]
        texto = resposta_modelo or _frase_pendencias(r)
        return Saida(" ".join(x for x in (texto_erros, texto) if x), ficha, s)
    if r.tipo != "pronta":
        return Saida(" ".join(x for x in (texto_erros, r.mensagem) if x), ficha, s)

    # Completa: executa sem esperar clique (somente leitura, já validada).
    from iativa.query import servico

    res = servico.executar_solicitacao(user, s)
    s = res.solicitacao or s
    # Executou sem esperar: descarta perguntas do modelo do tipo "posso executar?" e botões de confirmação.
    ficha["opcoes"] = []
    if resposta_modelo and re.search(r"\?\s*$|posso execut|deseja que eu execut|quer que eu execut", resposta_modelo, re.I):
        resposta_modelo = ""
    if res.ok:
        # M8: o histórico guarda só que rodou; os números ficam na tabela, em memória.
        base = resposta_modelo or f"Consultei {r.objetivo.lower()}."
        texto = f"{base}\n\nConsulta executada às {timezone.localtime():%H:%M}: {len(res.linhas)} linha(s)."
        ficha = {**ficha, "tipo": "executada", "estado": s.estado}
        return Saida(texto, ficha, s, res)
    return Saida(res.mensagem, {**ficha, "tipo": "falha_execucao", "estado": s.estado}, s)


def _aprender(user, d, texto, ficha) -> Saida:
    try:
        lista = candidatos(user, d.alvo or texto)
    except Exception:
        return Saida("Não consegui ler o dicionário do Protheus agora. Tente de novo em instantes.", ficha)
    if not lista:
        return Saida("Não achei tabelas relacionadas no dicionário. Pode me dizer outras palavras ou o código da "
                     "tabela (ex.: SRA)?", ficha)
    texto = (d.resposta + "\n\n" if d.resposta else "") + "Encontrei estas tabelas no dicionário. Escolha uma para eu aprender:"
    return Saida(texto, {**ficha, "tipo": "aprendizado", "candidatos": lista})


def _listar(administrador, objetivos, d, ficha) -> Saida:
    if administrador:
        modelos = ConsultaModelo.objects.exclude(situacao=SituacaoModelo.REMOVIDA)
        if not modelos:
            return Saida("Ainda não há consultas aprendidas.", ficha)
        linhas = [f"“{m.nome}” ({m.tabela}, {m.get_situacao_display().lower()})" for m in modelos]
    else:
        linhas = [f"“{o.nome}”" for o in objetivos if o.modelo_id]
        if not linhas:
            return Saida("Não há consultas aprendidas liberadas para você.", ficha)
    return Saida("Consultas aprendidas: " + "; ".join(linhas) + ".", ficha)


def _achar_modelo(alvo: str):
    alvo_n = _norm(alvo)
    modelos = list(ConsultaModelo.objects.exclude(situacao=SituacaoModelo.REMOVIDA))
    exatos = [m for m in modelos if alvo_n in (_norm(m.codigo), _norm(m.nome))]
    if exatos:
        return exatos
    palavras = [p for p in alvo_n.split() if len(p) >= 4 and p not in ("aprendizado", "consulta", "aprendida")]
    return [m for m in modelos if palavras and all(p[:5] in _norm(m.nome) for p in palavras)] or (
        [m for m in modelos if alvo_n and alvo_n in _norm(m.nome)])


def _remover(user, d, ficha) -> Saida:
    achados = _achar_modelo(d.alvo)
    if not achados:
        nomes = "; ".join(f"“{m.nome}”" for m in ConsultaModelo.objects.exclude(situacao=SituacaoModelo.REMOVIDA))
        return Saida("Não encontrei essa consulta aprendida." + (f" As que existem são: {nomes}." if nomes else ""), ficha)
    if len(achados) > 1:
        ficha["opcoes"] = [f"Remover {m.nome}" for m in achados[:6]]
        return Saida("Encontrei mais de uma. Qual delas?", ficha)
    cm = achados[0]
    if not d.confirmado:
        ficha["opcoes"] = [f"Sim, remover {cm.nome}", "Não"]
        return Saida(d.resposta or f"Confirma remover a consulta aprendida “{cm.nome}” ({cm.tabela})? "
                     "Ela deixa de aparecer para todos.", ficha)
    cm.situacao = SituacaoModelo.REMOVIDA
    cm.save(update_fields=["situacao"])
    registrar("parametro_revogado", "agente", usuario=user, entidade_id=cm.pk, estado_novo="modelo_removido")
    return Saida(f"Pronto, removi a consulta “{cm.nome}”. Ela não aparece mais para ninguém.", ficha)


def _lembrar(user, d, administrador, ficha) -> Saida:
    nota = d.nota.strip()
    if not nota:
        return Saida(d.resposta or "O que você quer que eu guarde?", ficha)
    if contem_cpf_cnpj(nota):
        return Saida("Não guardo CPF ou CNPJ na memória. Pode reformular sem esses dados?", ficha)
    escopo = EscopoNota.EMPRESA if administrador else EscopoNota.USUARIO
    NotaAgente.objects.create(texto=nota[:300], escopo=escopo, usuario=None if administrador else user,
                              criado_por=user)
    registrar("parametro_registrado", "agente", usuario=user, setor=setor_do_usuario(user), estado_novo="nota_memoria")
    alcance = "para todos" if administrador else "para você"
    return Saida(d.resposta or f"Anotado {alcance}: {nota}", ficha)
