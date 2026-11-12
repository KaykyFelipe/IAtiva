"""Agente conversacional (M10, ciclo fechado na M22).

A cada mensagem: monta o contexto (histórico curto, objetivos permitidos, estado da solicitação,
memória relevante, conhecimento aprovado) → o modelo escolhe UMA ação → o backend autoriza e
executa → a execução devolve uma observação estruturada (sucesso, vazio, parcial, erro de
parâmetros, erro de metadados, tempo esgotado, sem permissão) → se a falha é recuperável, o modelo
recebe a observação (sem dados do ERP) e escolhe outro caminho, dentro dos limites de passos,
tentativas e tempo → a resposta final é montada depois da execução.

O que é salvo: `Saida.texto` (intenção, critérios, estado). O que não é salvo: a resposta com
números, que vai em `Saida.resultado.mensagem` e existe só na resposta HTTP.

Regras que continuam valendo: lista fechada de objetivos, parâmetros validados no backend,
filiais autorizadas, compilador + validador AST, resultado só em memória (nunca no histórico
nem no modelo), auditoria só com códigos.
"""
import json
import re
import time
import unicodedata
from dataclasses import dataclass, field, replace
from datetime import datetime

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.utils import timezone

from iativa.accounts.filiais import codigo_por_texto, rotulo
from iativa.accounts.authz import Permissao, PermissaoNegada, filiais_autorizadas, setores_autorizados
from iativa.audit.servicos import registrar
from iativa.chat.estados import EM_PREPARACAO
from iativa.chat.models import EstadoSolicitacao as E
from iativa.chat.servicos import cancelar, criar_solicitacao
from iativa.memory.models import TipoValor
from iativa.memory.resolucao import RESOLVIDO, resolver

from . import memoria
from . import orquestrador as orq
from . import aprendizado
from .aprendizado import candidatos, pode_aprender, pode_aprender_sozinho
from .contratos import BUSCA, OBJETIVOS, objetivos_para
from .conversao import exibir
from .decisao import DecisaoInvalida, EntradaAgente, validar
from .models import ConsultaModelo, SituacaoModelo
from .provedores import FalhaModelo, obter_provedor

class Interrompido(Exception):
    """A pessoa clicou em "parar" enquanto a IA pensava: nada desta mensagem é aplicado."""


HISTORICO = 12
TENTATIVAS = 2


# Situações de uma observação (as de consulta vêm de `iativa.query.servico`).
SUCESSO, VAZIO, PARCIAL = "sucesso", "vazio", "parcial"
ERRO_PARAMETROS, ERRO_METADADOS = "erro_parametros", "erro_metadados"
TEMPO_ESGOTADO, FALHA_TRANSITORIA, SEM_PERMISSAO = "tempo_esgotado", "falha_transitoria", "sem_permissao"
RECUPERAVEIS = (ERRO_PARAMETROS, ERRO_METADADOS)  # o modelo pode tentar outro caminho
TRANSITORIAS = (TEMPO_ESGOTADO, FALHA_TRANSITORIA)  # o backend repete a mesma consulta, dentro do limite


@dataclass
class Observacao:
    """O que aconteceu com a ação. `detalhe` traz só motivo e nomes de campos/tabelas: nunca dados do ERP."""
    acao: str
    situacao: str
    detalhe: str = ""

    @property
    def recuperavel(self) -> bool:
        return self.situacao in RECUPERAVEIS

    def para_modelo(self) -> dict:
        return {"acao": self.acao, "situacao": self.situacao, "motivo": self.detalhe[:400]}


@dataclass
class Saida:
    texto: str  # persistido no histórico: sem números de resultado
    ficha: dict = field(default_factory=dict)
    solicitacao: object = None
    resultado: object = None  # só em memória; `resultado.mensagem` é a resposta final com números
    observacao: Observacao | None = None


def _ciclo() -> dict:
    return {"passos": 3, "retentativas": 1, "tempo_s": 120, **getattr(settings, "IATIVA_AGENTE_CICLO", {})}


def _assinatura(d) -> str:
    return json.dumps([d.acao, d.objetivo, sorted(d.parametros), d.alvo, d.nota, d.consulta, d.regra],
                      sort_keys=True, ensure_ascii=False, default=str)


def _norm(t: str) -> str:
    return unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower().strip()


def _ativa(user, c):
    return c.solicitacoes.filter(usuario=user, estado__in=list(EM_PREPARACAO)).order_by("-id").first()


def _filiais(user) -> list[str]:
    out = set()
    for sid in setores_autorizados(user, Permissao.CONSULTAR):
        from iativa.accounts.models import Department

        out |= filiais_autorizadas(user, Department.objects.get(pk=sid))
    return sorted(out) + (["TODAS"] if len(out) > 1 else [])


def _estado(user, s, defs) -> dict | None:
    if s is None:
        return None
    obj = OBJETIVOS[s.objetivo]
    definidos, faltando = {}, []
    for codigo in obj.parametros:
        d = defs.get(codigo)
        if d is None:
            continue
        r = resolver(d, user, s)
        if r.situacao == RESOLVIDO:
            # A busca veio da própria mensagem (o modelo já a viu); os demais dados pessoais ficam ocultos.
            mostrar = codigo == BUSCA or not d.contem_dado_pessoal
            definidos[d.nome] = exibir(r.valor) if mostrar else "[informado]"
        elif codigo in obj.obrigatorios:
            faltando.append(d.nome)
    return {"objetivo": s.objetivo, "nome": obj.nome, "estado": s.get_estado_display(),
            "ja_definido": definidos, "faltando": faltando, "completa": not faltando}


def _historico(c, ate=None) -> list[dict]:
    msgs = list(c.mensagens.order_by("-criado_em", "-id")[:HISTORICO + 1])[::-1]
    if ate is not None:
        msgs = [m for m in msgs if m.pk != ate]
    return [{"quem": "usuario" if m.papel == "usuario" else "iativa", "texto": m.texto[:600]} for m in msgs[-HISTORICO:]]


def _notas(user, texto: str) -> list[str]:
    """Notas relevantes ao pedido (não as 40 mais recentes), com o tipo de cada uma."""
    return [memoria.rotulo_nota(n) for n in memoria.notas_relevantes(user, texto)]


def _pendencias_contexto(user) -> list[dict]:
    try:
        return [{"ref": p.ref, "tipo": p.tipo, "descricao": p.descricao, "nivel": p.nivel,
                 "pode_decidir": p.pode_decidir} for p in memoria.pendencias(user)[:20]]
    except Exception:
        return []


def _catalogo(user, objetivos, defs) -> list[dict]:
    cat = orq._catalogo(objetivos, defs)
    filiais = _filiais(user)
    for o in cat:
        for p in o["parametros"]:
            if p["tipo"] == TipoValor.FILIAL:
                p["opcoes"] = filiais
                p["descricao"] = (p.get("descricao", "") + " Nomes: "
                                  + ", ".join(rotulo(f) for f in filiais) + ". Responda sempre o código; use TODAS quando a pessoa pedir todas as filiais.").strip()
    return cat


def _interpretar(provedor, entrada, codigos_obj, codigos_par):
    for _ in range(TENTATIVAS):
        try:
            return validar(provedor.decidir(entrada), codigos_obj, codigos_par)
        except FalhaModelo as exc:
            if exc.temporaria:
                raise  # o provedor já esperou e tentou outros modelos
        except DecisaoInvalida:
            continue
    raise FalhaModelo("sem decisão válida")


def conversar(user, c, texto: str, provedor=None, mensagem_usuario_id=None, interrompido=None) -> Saida:
    objetivos = objetivos_para(user)
    administrador = pode_aprender(user)
    if not objetivos and not administrador:
        registrar("acesso_negado", "agente", usuario=user, estado_novo="dominio", resultado="negado")
        return Saida("Seu usuário ainda não tem acesso a consultas. Fale com o administrador.")

    codigos_par = sorted({cod for o in objetivos for cod in o.parametros} | {"filial-analise"})
    defs = orq._definicoes(codigos_par)
    atual = _ativa(user, c)
    direta = _resposta_direta(user, atual, texto, defs)
    if direta is not None:
        # "001001" em resposta a "qual filial?": aplica sem passar pelo modelo (mais rápido e sem erro).
        registrar("interpretacao", "agente", usuario=user, estado_novo="resposta_direta", entidade_id=atual.pk, linhas=1)
        erros = orq._aplicar_parametros(user, atual, [direta], defs)
        atual.refresh_from_db()
        return _avaliar_e_executar(user, atual, "", erros, {"provedor": "direto", "opcoes": []})
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
        notas=_notas(user, texto), aprendidos=aprendidos, trechos=trechos, administrador=administrador,
        pendencias=_pendencias_contexto(user),
    )
    try:
        provedor = provedor or obter_provedor(usuario=user)
    except ImproperlyConfigured:
        return Saida("A conexão de IA precisa de configuração. Confira Conectar Claude e a liberação de privacidade do IAtiva.",
                     ficha={"tipo": "falha_modelo"})
    # Ciclo: agir → observar → corrigir (falha recuperável) ou concluir. Limites configuráveis.
    cfg = _ciclo()
    inicio = time.monotonic()
    feitas, observacoes, saida = set(), [], None
    for _ in range(max(1, int(cfg["passos"]))):
        entrada_passo = entrada if not observacoes else replace(
            entrada, solicitacao_atual=_estado(user, atual, defs), observacoes=[o.para_modelo() for o in observacoes])
        try:
            d = _interpretar(provedor, entrada_passo, [o.codigo for o in objetivos], codigos_par)
        except FalhaModelo as exc:
            if saida is not None:
                break  # a correção não veio: vale a resposta da tentativa anterior
            registrar("modelo_falhou", "agente", usuario=user, estado_novo=provedor.nome, resultado="erro")
            if provedor.nome == "claude-code":
                return Saida(str(exc), ficha={"tipo": "falha_modelo"})
            if exc.temporaria:
                return Saida("A IA (Gemini) está sobrecarregada ou no limite de uso do plano gratuito neste momento. "
                             "Tente de novo em cerca de um minuto, com a mesma pergunta.", ficha={"tipo": "falha_modelo"})
            return Saida("Não consegui processar sua mensagem agora. Pode repetir ou escrever de outro jeito?",
                         ficha={"tipo": "falha_modelo"})
        if interrompido is not None and interrompido():
            raise Interrompido  # antes de qualquer ação (e de consultar o ERP)
        assinatura = _assinatura(d)
        if assinatura in feitas:
            break  # a mesma ação com os mesmos dados já falhou: não repete
        feitas.add(assinatura)
        registrar("interpretacao", "agente", usuario=user, estado_novo=d.acao, entidade_id=atual.pk if atual else None,
                  linhas=len(d.parametros))
        saida = _agir(user, c, d, atual, defs, texto, provedor, entrada_passo, objetivos, administrador)
        if saida.observacao is None or not saida.observacao.recuperavel:
            return saida
        observacoes.append(saida.observacao)
        if time.monotonic() - inicio > float(cfg["tempo_s"]):
            break
        atual = _ativa(user, c)
    if len(observacoes) > 1:
        # Limite atingido sem resolver: diz o que foi tentado, em vez de uma recusa genérica.
        saida.texto = (f"{saida.texto} Tentei {len(observacoes)} caminhos nesta mensagem ("
                       + "; ".join(sorted({ROTULO_ACAO.get(o.acao, o.acao) for o in observacoes}))
                       + ") e parei no limite de tentativas.").strip()
        saida.ficha = {**saida.ficha, "tentativas": len(observacoes)}
    return saida


ROTULO_ACAO = {"preparar_consulta": "consultar", "executar_consulta": "executar a consulta",
               "aprender_tabela": "procurar a tabela no dicionário", "criar_consulta": "criar uma consulta nova",
               "lembrar": "guardar na memória"}


def _agir(user, c, d, atual, defs, texto, provedor, entrada, objetivos, administrador) -> Saida:
    """Executa UMA ação já autorizada e devolve a saída com a observação do que aconteceu."""
    ficha = {"provedor": provedor.nome, "opcoes": list(d.opcoes)}

    acao = d.acao
    if acao == "remover_aprendizado" and not administrador:
        return Saida("Essa ação é só para administradores.", ficha)
    if acao == "preparar_consulta":
        return _preparar(user, c, d, atual, defs, ficha, texto)
    if acao == "executar_consulta":
        if atual is None:
            # A anterior já terminou: o modelo quis "rodar de novo" (às vezes com critérios novos).
            # Prepara uma nova com o mesmo objetivo em vez de só anunciar e não executar.
            if d.objetivo or d.parametros or _ultima(user, c) is not None:
                return _preparar(user, c, d, None, defs, ficha, texto)
            return Saida(d.resposta or "Não há consulta em andamento. O que você quer consultar?", ficha)
        return _avaliar_e_executar(user, atual, d.resposta, [], ficha)
    if acao == "cancelar_consulta":
        if atual is not None:
            cancelar(user, atual)
        return Saida(d.resposta or ("Cancelei a consulta." if atual else "Não havia consulta em andamento."), ficha)
    if acao in ("aprender_tabela", "criar_consulta"):
        return _aprender(user, c, d, texto, ficha, provedor, entrada, atual)
    if acao == "listar_aprendizados":
        return _listar(administrador, objetivos, d, ficha)
    if acao == "remover_aprendizado":
        return _remover(user, d, ficha)
    if acao == "lembrar":
        return _lembrar(user, d, ficha)
    if acao == "listar_memorias":
        return _listar_memorias(user, ficha)
    if acao == "esquecer_memoria":
        return _esquecer(user, d, ficha)
    if acao == "listar_pendencias":
        return _listar_pendencias(user, ficha)
    if acao in ("aprovar_pendencia", "rejeitar_pendencia"):
        return _decidir_pendencia(user, d, acao == "aprovar_pendencia", ficha)
    return Saida(d.resposta or _ajuda(objetivos), ficha)


def _resposta_direta(user, atual, texto, defs):
    """Resposta curta que é exatamente uma das opções do único critério que falta (filial ou escolha).
    Devolve (código, valor) ou None."""
    if atual is None:
        return None
    t = (texto or "").strip().strip(".")
    if not t or len(t) > 40:
        return None
    estado = _estado(user, atual, defs)
    faltando = [cod for cod in OBJETIVOS[atual.objetivo].obrigatorios
                if defs.get(cod) is not None and defs[cod].nome in (estado or {}).get("faltando", [])]
    if len(faltando) != 1:
        return None
    d = defs[faltando[0]]
    if d.tipo == TipoValor.FILIAL:
        opcoes = _filiais(user)
    elif d.tipo == TipoValor.ESCOLHA:
        opcoes = list(d.opcoes or [])
    else:
        return None
    achado = next((o for o in opcoes if _norm(o) == _norm(t)), None)
    if achado is None and d.tipo == TipoValor.FILIAL:
        cod = codigo_por_texto(t)  # "Cuiabá" em resposta a "qual filial?"
        achado = cod if cod in opcoes else None
    return (d.codigo, achado) if achado else None


def _ajuda(objetivos) -> str:
    if not objetivos:
        return "Ainda não há consultas liberadas para você. Posso aprender uma nova se você me disser o que procura."
    return "Posso consultar: " + "; ".join(o.nome for o in objetivos) + ". O que você precisa?"


def _ultima(user, c):
    """Última solicitação desta conversa (qualquer estado), para continuar o mesmo assunto."""
    return c.solicitacoes.filter(usuario=user).exclude(objetivo="").order_by("-id").first()


def _herdar_criterios(user, origem, destino):
    """"e da Alcans?", "desde agosto": a nova consulta do mesmo assunto parte dos critérios ditos
    na anterior; os que vierem na mensagem substituem. A busca por nome não é herdada (o modelo a
    repete quando ainda vale), para "e todos?" voltar a trazer tudo."""
    from iativa.memory import servicos as memoria_valores
    from iativa.memory.models import Escopo, ParameterBinding, Situacao

    falhou = origem.estado != E.CONCLUIDA  # cálculo/filtro que derrubou a anterior não é herdado
    for b in ParameterBinding.objects.filter(solicitacao=origem, escopo=Escopo.SOLICITACAO,
                                             situacao=Situacao.APROVADA).select_related("definicao"):
        if b.definicao.codigo == BUSCA or b.definicao.codigo not in OBJETIVOS[destino.objetivo].parametros:
            continue
        if falhou and b.definicao.codigo in ("agregacao", "filtro-campo"):
            continue
        try:
            memoria_valores.registrar_valor(user, b.definicao, Escopo.SOLICITACAO, b.valor, solicitacao=destino)
        except Exception:  # valor que deixou de valer (ex.: filial): será perguntado de novo
            continue


def _preparar(user, c, d, atual, defs, ficha, texto="") -> Saida:
    anterior = _ultima(user, c) if atual is None else None
    objetivo = d.objetivo or (atual.objetivo if atual else (anterior.objetivo if anterior else ""))
    if not objetivo or objetivo not in OBJETIVOS:
        return Saida(d.resposta or "Que informação você procura?", ficha)
    if atual is not None and atual.objetivo != objetivo:
        cancelar(user, atual)  # novo assunto: a anterior é encerrada sem precisar clicar
        atual = None
    if atual is None:
        atual = criar_solicitacao(user, OBJETIVOS[objetivo].dominio, objetivo, conversa=c)
        if anterior is not None and anterior.objetivo == objetivo:
            _herdar_criterios(user, anterior, atual)
    pares = list(d.parametros)
    if "periodo-analise" in OBJETIVOS[objetivo].parametros and not any(c == "periodo-analise" for c, _ in pares):
        # O modelo não mandou período: usa o que a mensagem diz ("desde janeiro"), se disser.
        from .periodo import periodo_da_mensagem

        lido = periodo_da_mensagem(texto, timezone.localdate())
        if lido:
            pares.append(("periodo-analise", lido))
    erros = orq._aplicar_parametros(user, atual, pares, defs)
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


def _obs_erros(texto_erros: str) -> Observacao:
    """Valor recusado: em geral dá para corrigir pelo contexto. Filial fora das autorizadas é falta de
    permissão: encerra, sem trocar a filial por outra nem tentar de novo."""
    situacao = SEM_PERMISSAO if "filiais autorizadas" in texto_erros else ERRO_PARAMETROS
    return Observacao("preparar_consulta", situacao, texto_erros)


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
        # Falta uma decisão da pessoa: pergunta. Só há o que corrigir sozinha se um valor foi recusado.
        obs = _obs_erros(texto_erros) if texto_erros else None
        return Saida(" ".join(x for x in (texto_erros, texto) if x), ficha, s, observacao=obs)
    if r.tipo != "pronta":
        situacao = ERRO_METADADOS if r.mensagem == orq.MOTIVOS["metadados_indisponiveis"] else SEM_PERMISSAO
        obs = Observacao("preparar_consulta", TEMPO_ESGOTADO if situacao == ERRO_METADADOS else situacao, r.mensagem)
        return Saida(" ".join(x for x in (texto_erros, r.mensagem) if x), ficha, s, observacao=obs)
    if texto_erros:
        # Um critério pedido não foi aceito: executar sem ele traria outro resultado (ex.: sem filtro).
        return Saida(f"{texto_erros} Me diga o valor certo e eu executo.", ficha, s, observacao=_obs_erros(texto_erros))

    # Completa: executa sem esperar clique (somente leitura, já validada).
    from iativa.query import servico

    res = servico.executar_solicitacao(user, s)
    repeticoes = 0
    while not res.ok and res.situacao in TRANSITORIAS and repeticoes < int(_ciclo()["retentativas"]):
        # Erro transitório (rede, tempo): repete a mesma consulta, com as mesmas permissões reconferidas.
        repeticoes += 1
        nova = servico.repetir(user, res.solicitacao or s, conversa=s.conversa)
        if orq.avaliar(user, nova).tipo != "pronta":
            break
        nova.refresh_from_db()
        res = servico.executar_solicitacao(user, nova)
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
        from iativa.query.consolidado import talvez_consolidar

        exibido = talvez_consolidar(user, s, res)
        if exibido is not res:  # chat mestre: um bloco por setor (Etapa 6)
            texto = (f"{base}\n\nConsulta executada às {timezone.localtime():%H:%M} em {len(exibido.blocos)} "
                     "setor(es).")
        return Saida(texto, ficha, s, exibido, Observacao("executar_consulta", res.situacao or SUCESSO))
    mensagem = res.mensagem + (f" Tentei {repeticoes + 1} vezes." if repeticoes else "")
    return Saida(mensagem, {**ficha, "tipo": "falha_execucao", "estado": s.estado}, s,
                 observacao=Observacao("executar_consulta", res.situacao or FALHA_TRANSITORIA, res.mensagem))


def _aprender(user, c, d, texto, ficha, provedor=None, entrada=None, atual=None) -> Saida:
    """M18: a IA acha a tabela, monta a consulta, o sistema cria/aprova sozinho e já executa.
    Se algo falhar, cai na lista de tabelas para escolher à mão (administrador)."""
    motivo = []
    if provedor is not None and entrada is not None and pode_aprender_sozinho(user):
        try:
            saida = _aprender_sozinho(user, c, d, texto, ficha, provedor, entrada, atual, motivo)
        except FalhaModelo:
            saida = None
            motivo.append("o modelo não devolveu uma consulta válida")
        if saida is not None:
            return saida
    elif not pode_aprender(user):
        return Saida("Não há consulta liberada para isso e o seu usuário não pode criar consultas novas. "
                     "Um administrador precisa criar e liberar essa consulta para o seu setor.", ficha,
                     observacao=Observacao(d.acao, SEM_PERMISSAO, "usuário sem permissão para criar consultas"))
    # Falha recuperável: o modelo recebe o motivo e pode tentar outra tabela ou outros campos.
    obs = Observacao(d.acao, ERRO_METADADOS, "; ".join(motivo) or "nenhuma tabela adequada encontrada com esses termos")
    if not pode_aprender(user):
        return Saida("Procurei no dicionário do Protheus e não consegui montar essa consulta ("
                     + obs.detalhe + "). Diga outras palavras para o assunto ou o código da tabela (ex.: SRA).",
                     ficha, observacao=obs)
    try:
        lista = candidatos(user, d.alvo or texto)
    except Exception:
        return Saida("Não consegui ler o dicionário do Protheus agora. Tente de novo em instantes.", ficha,
                     observacao=Observacao(d.acao, TEMPO_ESGOTADO, "dicionário do ERP indisponível"))
    if not lista:
        return Saida("Não achei tabelas relacionadas no dicionário. Pode me dizer outras palavras ou o código da "
                     "tabela (ex.: SRA)?", ficha, observacao=obs)
    texto = (d.resposta + "\n\n" if d.resposta else "") + "Encontrei estas tabelas no dicionário. Escolha uma para eu aprender:"
    return Saida(texto, {**ficha, "tipo": "aprendizado", "candidatos": lista}, observacao=obs)


def _campos_para_modelo(user, alias):
    info = aprendizado.campos_da_tabela(user, alias, automatico=True)
    campos = [{"campo": f.nome, "tipo": {"D": "data", "N": "numero"}.get(f.tipo, "texto"), "titulo": f.titulo}
              for f in info["campos"] if not aprendizado.sensivel(f)]
    try:
        juncoes = [{"tabela": j["tabela"], "nome": j["nome"],
                    "campos": [{"campo": f.nome, "titulo": f.titulo} for f in j["campos"]][:40]}
                   for j in aprendizado.juncoes_possiveis(user, alias)][:6]
    except Exception:
        juncoes = []
    return {"tabela": info["alias"], "nome": info["nome"], "campos": campos[:150], "juncoes_possiveis": juncoes}


def _aprender_sozinho(user, c, d, texto, ficha, provedor, entrada, atual, motivo=None):
    motivo = motivo if motivo is not None else []
    consulta = d.consulta
    if consulta is None:
        try:
            lista = candidatos(user, d.alvo or texto, limite=3, automatico=True)
        except Exception:
            motivo.append("não foi possível ler o dicionário do ERP")
            return None
        tabelas = []
        for item in lista:
            try:
                tabelas.append(_campos_para_modelo(user, item["alias"]))
            except Exception:
                continue  # sem campo filial, restrita etc.
        if not tabelas:
            motivo.append(f"nenhuma tabela utilizável para “{(d.alvo or texto)[:60]}”")
            return None
        entrada2 = replace(entrada, tabelas_para_aprender=tabelas)
        codigos_par = sorted({p["codigo"] for o in entrada.objetivos for p in o.get("parametros", [])}
                             | {"filial-analise", "periodo-analise", "texto-busca", "filtro-campo"})
        d2 = _interpretar(provedor, entrada2, [o["codigo"] for o in entrada.objetivos], codigos_par)
        if d2.acao != "criar_consulta" or d2.consulta is None:
            motivo.append("as tabelas encontradas (" + ", ".join(t["tabela"] for t in tabelas) + ") não serviram")
            return None
        d, consulta = d2, d2.consulta
    consulta = {**consulta, "campo_data": consulta.get("campo_data", "").upper(),
                "campo_busca": consulta.get("campo_busca", "").upper()}
    try:
        cm = aprendizado.criar_automatico(user, consulta)
    except (ValidationError, PermissaoNegada) as exc:
        registrar("modelo_falhou", "agente", usuario=user, estado_novo="aprendizado_automatico", resultado="erro")
        detalhe = "; ".join(getattr(exc, "messages", None) or [str(exc)])
        motivo.append(f"consulta sobre {consulta.get('tabela', '')} recusada: {detalhe}"[:300])
        return None
    registrar("interpretacao", "agente", usuario=user, estado_novo="aprendeu_sozinho", entidade_id=cm.pk)
    codigos = set(OBJETIVOS[cm.codigo].parametros)
    defs = orq._definicoes(sorted(codigos))
    d3 = replace(d, acao="preparar_consulta", objetivo=cm.codigo,
                 parametros=tuple((k, v) for k, v in d.parametros if k in codigos),
                 resposta=(f"Aprendi a consultar “{cm.nome}” ({cm.tabela}). " + (d.resposta or "")).strip())
    return _preparar(user, c, d3, atual, defs, ficha, texto)


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


def _lembrar(user, d, ficha) -> Saida:
    nota = d.nota.strip()
    if not nota:
        return Saida(d.resposta or "O que você quer que eu guarde?", ficha)
    validade = None
    if d.validade:
        try:
            validade = datetime.strptime(d.validade, "%d/%m/%Y").date()
        except ValueError:
            validade = None
    try:
        n = memoria.lembrar(user, nota, d.alcance, tipo=d.tipo_nota, regra=d.regra, confirmado=d.confirmado,
                            valido_ate=validade)
    except ValidationError as exc:
        texto = exc.messages[0]
        if "CPF" in texto:
            texto += " Pode reformular sem esses dados?"
        return Saida(texto, ficha)
    except Exception:  # PermissaoNegada
        return Saida("Não consegui guardar isso para o setor: seu usuário está sem setor ativo.", ficha)
    c = n.criterios or {}
    criterio = (f"nas consultas sobre {c['tabela']}, só registros com {c['campo']} "
                f"{'igual a' if c['op'] == '=' else 'diferente de'} “{c['valor']}”") if c else ""
    if n.tipo == "hipotese" and c:
        ficha["opcoes"] = ["Sim, confirmo", "Não"]
        return Saida(f"Entendi assim: {criterio}. Confirma? Só passo a aplicar depois da sua confirmação.", ficha)
    if n.tipo == "hipotese":
        return Saida(f"Guardei como hipótese, ainda sem aplicar: “{nota}”. Para eu aplicar sozinha nas consultas, "
                     "preciso saber qual campo indica isso e qual valor ele tem (por exemplo, o campo de situação "
                     "e o código que marca cancelado).", ficha)
    if n.tipo == "regra" and n.situacao == "aprovada":
        prazo = f" até {n.valido_ate:%d/%m/%Y}" if n.valido_ate else ""
        return Saida(f"Regra registrada{prazo}: {criterio}. Já vale nas próximas consultas e aparece nos critérios.",
                     ficha)
    if n.situacao == "aprovada":
        alcance = {"usuario": "para você", "setor": f"para o setor {n.setor.nome if n.setor else ''}".strip(),
                   "empresa": "para todos"}[n.escopo]
        return Saida(d.resposta if d.resposta and "aprova" not in d.resposta.lower() else f"Anotado {alcance}: {nota}",
                     ficha)
    extra = " Ela substitui a definição atual do mesmo termo depois de aprovada." if n.substitui_id else ""
    nivel = f"do setor {n.setor.nome}" if n.setor else "da empresa"
    return Saida(f"Registrei como proposta para a memória {nivel}: “{nota}”. Ela só vale depois de aprovada por "
                 f"quem aprova definições nesse nível.{extra}", ficha)


def _listar_memorias(user, ficha) -> Saida:
    notas = memoria.minhas_memorias(user)
    if not notas:
        return Saida("Ainda não guardei nada na memória para você.", ficha)
    nivel = {"usuario": "só sua", "setor": "do setor", "empresa": "da empresa"}
    linhas = [f"[nota-{n.pk}] ({n.get_tipo_display().lower()}, {nivel[n.escopo]}"
              + (f", até {n.valido_ate:%d/%m/%Y}" if n.valido_ate else "") + f") {n.texto}" for n in notas[:20]]
    return Saida("O que está na memória e vale para você: " + "; ".join(linhas)
                 + ". Para tirar uma, diga “esqueça nota-N”; para corrigir, me ensine de novo.", ficha)


def _esquecer(user, d, ficha) -> Saida:
    achadas = memoria.achar_memoria(user, d.alvo or d.nota)
    if not achadas:
        return Saida("Não encontrei essa nota na memória. Peça “o que você tem na memória?” para ver a lista.", ficha)
    if len(achadas) > 1:
        ficha["opcoes"] = [f"Esquecer nota-{n.pk}" for n in achadas[:6]]
        return Saida("Encontrei mais de uma nota. Qual delas?", ficha)
    n = achadas[0]
    if not memoria.pode_desativar(user, n):
        return Saida("Essa nota é do setor ou da empresa: só quem aprova definições nesse nível pode desativá-la.",
                     ficha, observacao=Observacao("esquecer_memoria", SEM_PERMISSAO, "nota de outro nível"))
    if not d.confirmado:
        ficha["opcoes"] = [f"Sim, esquecer nota-{n.pk}", "Não"]
        return Saida(f"Confirma tirar da memória “{n.texto}”?", ficha)
    memoria.desativar(user, n)
    return Saida(f"Pronto: “{n.texto}” não vale mais.", ficha)


def _listar_pendencias(user, ficha) -> Saida:
    lista = memoria.pendencias(user)
    if not lista:
        return Saida("Não há propostas aguardando aprovação para você.", ficha)
    linhas = []
    for p in lista[:10]:
        atual = f" (hoje vale: {p.atual})" if p.atual else ""
        linhas.append(f"{p.tipo} [{p.ref}], {p.nivel}: “{p.descricao}”{atual}")
    ficha["opcoes"] = [f"Aprovar {p.ref}" for p in lista if p.pode_decidir][:6]
    return Saida("Propostas aguardando aprovação: " + "; ".join(linhas) + ".", ficha)


def _decidir_pendencia(user, d, aprovar: bool, ficha) -> Saida:
    achados = memoria.achar_pendencia(user, d.alvo)
    verbo = "aprovar" if aprovar else "rejeitar"
    if not achados:
        return Saida("Não encontrei essa proposta na fila. Peça “quais propostas estão pendentes?” para ver a lista.",
                     ficha)
    if len(achados) > 1:
        ficha["opcoes"] = [f"{verbo.capitalize()} {p.ref}" for p in achados[:6]]
        return Saida("Encontrei mais de uma proposta. Qual delas?", ficha)
    p = achados[0]
    if aprovar and not p.pode_decidir:
        return Saida("Você não pode aprovar essa proposta: é preciso ter “aprovar definições” no nível "
                     f"({p.nivel}) e não ser quem a propôs.", ficha)
    if not d.confirmado:
        atual = f" Hoje vale “{p.atual}”; ela será substituída." if p.atual and aprovar else ""
        ficha["opcoes"] = [f"Sim, {verbo} {p.ref}", "Não"]
        return Saida(f"Confirma {verbo} {p.tipo.lower()} “{p.descricao}” ({p.nivel})?{atual}", ficha)
    try:
        memoria.decidir(user, p.ref, aprovar)
    except Exception as exc:
        msg = getattr(exc, "messages", None)
        return Saida(f"Não consegui {verbo}: {msg[0] if msg else exc}", ficha)
    feito = "aprovada; já vale" if aprovar else "rejeitada"
    return Saida(f"Pronto: proposta “{p.descricao}” {feito}.", ficha)
