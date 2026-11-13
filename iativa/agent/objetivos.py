"""Coordenação geral de objetivos (M38): o pedido só termina com evidência de que foi cumprido.

Ideia (adaptada do runtime do OpenMausBot, sem copiar transcrições nem resultados):
- cada pedido tem uma EXIGÊNCIA (o que precisa existir para ele estar cumprido) e cada ação
  executada produz uma EVIDÊNCIA (o que de fato foi entregue). Consulta que deu certo é evidência
  de "consulta", não de "verificação" nem de "investigação";
- enquanto a evidência não cobre a exigência, o ciclo continua, com uma observação que diz o que
  falta. Texto que só anuncia uma ação, ou que repete a resposta anterior para outra pergunta,
  não conta como progresso e volta ao modelo;
- sem progresso em passos seguidos, o ciclo para (não insiste): vira diagnóstico sanitizado para a
  manutenção (capacidade ausente, mesmo sem exceção) e o pedido fica aguardando a correção;
- limite de passos/tempo com trabalho ainda andando: o objetivo é preservado e o worker continua,
  sem outra mensagem da pessoa. Falta de permissão e pergunta à pessoa são paradas legítimas.

Persistido (tabela Objetivo): exigência, situação, trilha de códigos (ação:situação:evidência) e
contadores. Nunca o texto de respostas, linhas, nomes ou números do ERP.
"""
import difflib
import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import timedelta

from django.conf import settings
from django.db.models import Q, F
from django.utils import timezone

from .contratos import FIXOS
from .models import Objetivo, SituacaoObjetivo as SO

# Exigências, da mais fraca para as específicas.
RESPOSTA, CONSULTA, CALCULO = "resposta", "consulta", "calculo"
VERIFICACAO, INVESTIGACAO, HISTORICO = "verificacao", "investigacao", "historico"
EXIGENCIAS = (RESPOSTA, CONSULTA, CALCULO, VERIFICACAO, INVESTIGACAO, HISTORICO)
# Evidências que não entregam nada, mas encerram legitimamente o turno.
PERGUNTA, BLOQUEIO, ACAO = "pergunta", "bloqueio", "acao"
DELEGADA = "tarefa_agendada"

SATISFAZ = {
    RESPOSTA: {RESPOSTA, CONSULTA, CALCULO, VERIFICACAO, INVESTIGACAO, HISTORICO, ACAO},
    CONSULTA: {CONSULTA, CALCULO, VERIFICACAO, INVESTIGACAO, HISTORICO},
    CALCULO: {CALCULO, INVESTIGACAO},
    VERIFICACAO: {VERIFICACAO},
    INVESTIGACAO: {INVESTIGACAO},
    HISTORICO: {HISTORICO},
}
FALTA = {
    CONSULTA: "A consulta pedida ainda não foi feita",
    CALCULO: "O cálculo pedido ainda não foi feito",
    VERIFICACAO: "A verificação pedida ainda não foi feita: só a lista foi consultada",
    INVESTIGACAO: "A investigação pedida ainda não foi concluída",
    HISTORICO: "O histórico pedido ainda não foi levantado",
    RESPOSTA: "O pedido ainda não foi respondido",
}
COMO = {
    CONSULTA: "use preparar_consulta (ou aprender_tabela, se nenhuma consulta atende)",
    CALCULO: "use preparar_consulta com agregacao ou calcular_resultado sobre colunas/intervalos autorizados",
    VERIFICACAO: "use investigar_lista com os campos de lista_anterior",
    INVESTIGACAO: "use investigar_divergencia",
    HISTORICO: "use historico_cargo",
    RESPOSTA: "responda com o que foi verificado",
}
# Ação que, por si, declara o que o pedido exige (quando o modelo não declarou e nenhum detector falou).
PELA_ACAO = {"investigar_lista": VERIFICACAO, "investigar_divergencia": INVESTIGACAO, "historico_cargo": HISTORICO,
             "preparar_consulta": CONSULTA, "executar_consulta": CONSULTA, "aprender_tabela": CONSULTA,
             "criar_consulta": CONSULTA}

TEXTO_REPETIDA_ACAO = ("Essa mesma ação, com os mesmos dados, já foi feita nesta mensagem. Não repita: escolha o "
                       "próximo passo que falta (ex.: ajustar_filtro na consulta criada, ou preparar_consulta com "
                       "agregacao), ou responda com o que já foi verificado.")
FALTA_MEMORIA = ("A pessoa também pediu para guardar o que ensinou, e isso ainda não foi feito. Use agora a ação "
                 "lembrar: nota curta e reutilizável (ex.: qual consulta responde ao termo e com qual filtro), "
                 "tipo_nota termo ou regra, e o alcance pedido (empresa, setor ou usuário). Não refaça a consulta.")
_MEMORIA_RE = re.compile(r"\b(guard[ae]|lembr[ae]|memoriz|anot[ae]|grav[ae]|daqui (pra|para) frente|"
                         r"a partir de agora|de agora em diante|sempre que)", re.I)


FALTA_PARTE = ("A mensagem tem mais de uma pergunta e a resposta acima cobriu só parte dela. Responda agora a "
               "próxima pergunta ainda não respondida (execute a consulta dela; não refaça a anterior). "
               "Não diga que já respondeu sem executar a consulta.")
_PERGUNTA_RE = re.compile(r"(?:^|[?;,.]|\be\b)\s*(?:e\s+)?(?:quant[oa]s?|qua(?:l|is)|quem|onde|quando)\b", re.I)


def partes(texto: str) -> int:
    """M41: quantas perguntas a mensagem faz ("quantos entraram? e quantos saíram?" = 2)."""
    return max(1, len(_PERGUNTA_RE.findall(texto or "")))


def falta_parte(texto: str, feitas: list[str]) -> str:
    """Dica com as perguntas da mensagem e as consultas já feitas, para o modelo atacar a pergunta que
    falta (em qualquer ordem) em vez de repetir uma já respondida."""
    texto = texto or ""
    inicios = [m.start() for m in _PERGUNTA_RE.finditer(texto)] or [0]
    pedacos = [texto[a:b].strip(" ?;,.") for a, b in zip(inicios, inicios[1:] + [len(texto)])]
    lista = " ".join(f"{i}) {p}" for i, p in enumerate((p for p in pedacos if p), 1))
    return (f"{FALTA_PARTE} Perguntas: {lista}. Já executado nesta mensagem: {'; '.join(feitas) or 'nada'}. "
            "Escolha a pergunta que essas consultas NÃO respondem e execute a consulta dela (outro objetivo "
            "ou outros critérios); se nenhuma consulta existente responde, crie uma.")


def pede_memoria(texto: str) -> bool:
    """A mensagem pede para guardar um ensinamento (além de, talvez, responder)."""
    return bool(_MEMORIA_RE.search(texto or ""))


ANUNCIO = "anuncio_sem_execucao"
REPETIDA = "resposta_repetida"
ETAPA = "etapa_concluida"
TEXTO_OBS = {
    ANUNCIO: ("Você disse o que faria, mas não escolheu a ação que faz isso. Escolha agora a ação que executa o "
              "trabalho que falta, ou responda com o resultado já verificado. Não repita uma ação que já deu certo."),
    REPETIDA: ("Essa é a mesma resposta que a pessoa já recebeu para outra pergunta. Ela pediu algo diferente: mude "
               "a estratégia (outra ação, outra consulta, outra tabela) ou pergunte o que falta saber."),
}


def cfg() -> dict:
    return {"cutucadas": 2, "sem_progresso": 2, "continuacoes": 2, "espera_s": 45,
            **{k: v for k, v in getattr(settings, "IATIVA_AGENTE_CICLO", {}).items()
               if k in ("cutucadas", "sem_progresso", "continuacoes", "espera_s")}}


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"\d{1,2}:\d{2}", "", t).split())


# --- o que o pedido exige -----------------------------------------------------------------------

def _detectores():
    """(exigência, função do texto normalizado). O backend garante o mínimo mesmo se o modelo errar."""
    from . import diagnostico, historico_funcional

    return (
        (INVESTIGACAO, lambda bruto, t: diagnostico.solicitado(bruto)),
        (CALCULO, lambda bruto, t: historico_funcional.pede_tempo_estagio(bruto) or
         (bool(re.search(r'\bdias?\s+(?:uteis|util|corridos?)\b', t)) and not re.search(r'^o que|^explique|^defina', t))),
        (HISTORICO, lambda bruto, t: historico_funcional.solicitado(bruto)),
        (VERIFICACAO, lambda bruto, t: _verificar_pessoas(t)),
    )


def _verificar_pessoas(t):
    pessoas = re.search(r'\b(demitid|desligad|readmit|funcionari|pessoas|matricula|vinculo)\w*', t)
    if not pessoas:
        return False
    relacao = re.search(r'\bse\b|readmit|matricula\s+nova|nova\s+matricula|\bforam\b.*\bmesmo\b', t)
    if re.search(r'\b(quantos|quantas|conte|contagem|total|some|soma)\b', t) and not relacao:
        return False
    return bool(re.search(r'\b(verifi|confir|confer|chec|investig|descubr)\w*', t) or
                re.search(r'\b(foram|estao|tiveram)\b.*\b(mesmo|nova|readmit)\w*', t))


def exigencia(texto: str, decisao=None) -> tuple[str, str]:
    """(exigência, origem). Ordem: detector do backend, declaração do modelo, ação escolhida."""
    t = _norm(texto)
    for exig, quando in _detectores():
        try:
            if quando(texto, t):
                return exig, "detector"
        except Exception:
            continue
    declarada = getattr(decisao, "entrega", "") if decisao is not None else ""
    if declarada in EXIGENCIAS:
        if declarada == CONSULTA and any(k == "agregacao" for k, _ in getattr(decisao, "parametros", ())):
            return CALCULO, "modelo"
        return declarada, "modelo"
    acao = getattr(decisao, "acao", "") if decisao is not None else ""
    if acao in PELA_ACAO:
        if PELA_ACAO[acao] == CONSULTA and any(k == "agregacao" for k, _ in getattr(decisao, "parametros", ())):
            return CALCULO, "acao"
        return PELA_ACAO[acao], "acao"
    return RESPOSTA, "acao"


# --- o que a ação entregou ----------------------------------------------------------------------

def evidencia(decisao, saida, texto='') -> str:
    """Tipo de evidência que a saída comprova. Vazio = nada entregue (falha ou etapa sem resultado)."""
    ficha = saida.ficha or {}
    tipo = ficha.get("tipo", "")
    obs = saida.observacao
    if obs is not None and obs.situacao == "sem_permissao":
        return BLOQUEIO
    if tipo == "tarefa":
        return DELEGADA
    if tipo == 'impedimento_dados':
        return BLOQUEIO
    unidade = metrica_exigida(texto)
    if unidade and getattr(saida.resultado, 'ok', False) and getattr(saida.resultado, 'medida_calculada', '') != unidade:
        return CONSULTA  # contagem de registros não comprova dias úteis/corridos
    if tipo == "diagnostico":
        return INVESTIGACAO if ficha.get("investigacao_completa") else ""
    if tipo == "historico_cargo" and saida.resultado is not None:
        return CALCULO if getattr(saida.resultado, 'medida_calculada', '') else HISTORICO
    if tipo == "bloqueada":
        return BLOQUEIO
    res = saida.resultado
    if res is not None and (getattr(res, "ok", False) or getattr(res, "consolidado", False)):
        if getattr(res, 'medida_calculada', ''):
            return CALCULO
        calculo = any(k == "agregacao" for k, _ in getattr(decisao, "parametros", ())) or bool(
            getattr(getattr(res, "totais", None), "medidas", None) and len(getattr(res, "linhas", [])) <= 1
            and any("Soma" in c or "Média" in c or "Quantidade" in c for c in getattr(res, "colunas", [])))
        if not calculo and getattr(decisao, "objetivo", "") in FIXOS and getattr(
                getattr(res, "totais", None), "medidas", None):
            # M41: consulta fixa (títulos a vencer, em atraso) já traz o total geral calculado no banco:
            # "quanto vamos receber?" está respondido, sem criar consulta nova para somar de novo.
            calculo = True
        return CALCULO if calculo else CONSULTA
    if obs is not None and obs.situacao not in ("sucesso", "vazio", "parcial", ETAPA):
        return ""
    texto = (saida.texto or "").strip()
    if texto.endswith("?") or ficha.get("opcoes") or ficha.get("estado") == "aguardando_parametros":
        return PERGUNTA
    acao = getattr(decisao, "acao", "")
    if acao == "conversar":
        return RESPOSTA
    if acao in ("lembrar", "listar_memorias", "esquecer_memoria", "listar_aprendizados", "remover_aprendizado",
                "listar_pendencias", "aprovar_pendencia", "rejeitar_pendencia", "cancelar_consulta",
                "ajustar_filtro", "voltar_filtro"):
        return ACAO
    return ""


def metrica_exigida(texto):
    t = _norm(texto)
    if re.search(r'\bdias?\s+(?:uteis|util)\b', t):
        return 'dias_uteis'
    return 'dias_corridos' if re.search(r'\bdias?\s+corridos?\b', t) else ''


def cumpre(exig: str, ev: str) -> bool:
    return ev in SATISFAZ.get(exig, set())


# --- texto que não é progresso --------------------------------------------------------------------

_VERBOS = (r"consult|verific|busc|calcul|cont|som|investig|confer|rod|execut|levant|analis|refaz|list|chec|procur|"
           r"compar|cruz|apur|pesquis")
_ANUNCIO_INICIO = re.compile(
    rf"^(?:agora |ja |entao |certo[,.]? |ok[,.]? )?(?:vou|irei|vamos|deixa eu|deixe-me|estou|estarei)\s+(?:agora\s+|ja\s+)?"
    rf"(?:{_VERBOS})\w*", re.I)
_ANUNCIO_GERUNDIO = re.compile(rf"^(?:{_VERBOS})\w*ndo\b", re.I)
_ANUNCIO_ESPERA = re.compile(r"^(?:um momento|so um instante|aguarde|ja retorno|ja volto|volto em seguida)\b", re.I)


def anuncia(texto: str) -> bool:
    """Texto que só promete uma ação ("Vou consultar…", "Verificando…") sem trazer resultado nem pergunta."""
    bruto = (texto or "").strip()
    if not bruto or "?" in bruto or len(bruto) > 4000:
        return False
    trecho = bruto if len(bruto) <= 400 else bruto.split("\n\n")[-1].strip()
    if len(trecho) > 400:
        return False
    t = _norm(trecho)
    return bool(_ANUNCIO_INICIO.search(t) or _ANUNCIO_GERUNDIO.search(t) or _ANUNCIO_ESPERA.search(t)
                or re.search(r"(?::|\.\.\.|…)$", trecho))


def _parecido(a: str, b: str) -> bool:
    a, b = _norm(a), _norm(b)
    return bool(a) and bool(b) and difflib.SequenceMatcher(None, a, b).ratio() >= 0.92


def repetida(resposta: str, conversa, mensagem_atual_id=None, pergunta_atual: str = "") -> bool:
    """Mesma resposta que a pessoa já recebeu há pouco, para uma pergunta DIFERENTE da de agora."""
    if conversa is None or len(_norm(resposta)) < 60:
        return False
    msgs = list(conversa.mensagens.order_by("-id")[:8])
    if mensagem_atual_id is not None:
        msgs = [m for m in msgs if m.pk != mensagem_atual_id]
    for i, m in enumerate(msgs):
        if m.papel == "usuario" or not _parecido(resposta, m.texto):
            continue
        anterior = next((x for x in msgs[i + 1:] if x.papel == "usuario"), None)
        if anterior is not None and _parecido(pergunta_atual, anterior.texto):
            return False  # mesma pergunta de novo: a mesma resposta é legítima
        return True
    return False


def texto_sem_acao(decisao, conversa, mensagem_atual_id, pergunta: str) -> str:
    if getattr(decisao, "acao", "") != "conversar":
        return ""
    if anuncia(decisao.resposta):
        return ANUNCIO
    if repetida(decisao.resposta, conversa, mensagem_atual_id, pergunta):
        return REPETIDA
    return ""


# --- acompanhamento de um pedido dentro do ciclo -------------------------------------------------

@dataclass
class Acompanhamento:
    """Estado do objetivo durante o ciclo (em memória); vira registro só quando há o que preservar."""
    texto: str
    exig: str = ""
    origem: str = ""
    trilha: list = field(default_factory=list)  # "acao:situacao:evidencia"
    cutucadas: int = 0
    memoria_feita: bool = False  # M40: a ação lembrar já rodou nesta mensagem
    entregue: str = ""  # M40: evidência que já cumpria o pedido antes do passo de memória
    partes_faltam: int = -1  # M41: perguntas da mensagem ainda sem resposta (-1 = ainda não contadas)
    formato: str = ""  # M48: como a pessoa quer a resposta ("direto" = só o valor; "resumo")
    parado: int = 0  # passos seguidos sem evidência nova
    melhor: str = ""  # melhor evidência obtida
    registro: Objetivo | None = None  # preenchido na continuação pelo worker

    def definir(self, decisao):
        if getattr(decisao, "formato", ""):
            self.formato = decisao.formato
        if not self.exig:
            self.exig, self.origem = exigencia(self.texto, decisao)

    def anotar(self, acao: str, situacao: str, ev: str = ""):
        self.trilha = (self.trilha + [f"{acao or '-'}:{situacao or '-'}:{ev or '-'}"])[-30:]
        if ev and ev not in (PERGUNTA, BLOQUEIO):
            self.parado = 0
            self.melhor = ev
        else:
            self.parado += 1

    def pode_cutucar(self) -> bool:
        return self.cutucadas < int(cfg()["cutucadas"]) and not self.estagnado()

    def estagnado(self) -> bool:
        return self.parado >= int(cfg()["sem_progresso"])

    def cumprido(self, ev: str) -> bool:
        return cumpre(self.exig or RESPOSTA, ev)

    def falta_obs(self, consulta=None) -> str:
        txt = (f"{FALTA[self.exig]}. A etapa anterior deu certo, mas é só parte do pedido: {COMO[self.exig]}. Só pare "
               "para perguntar à pessoa (conversar terminando em pergunta) se faltar uma decisão que só ela pode dar.")
        metrica = metrica_exigida(self.texto)
        if metrica:
            return txt + (f' A unidade pedida é {metrica}: listar datas ou contar registros não basta. '
                          'Use calcular_resultado com essa operação e as colunas inicial/final, ou historico_cargo '
                          'para resolver os intervalos de estágio. Preserve pessoa, período e filiais do pedido.')
        if consulta:
            txt += (f" Consulta usada na etapa anterior: {consulta['codigo']}"
                    + (f" (filtros atuais: {consulta['filtros']})" if consulta.get("filtros") else "") + ". "
                    "Reutilize-a como objetivo em vez de procurar a tabela de novo. Se a pessoa pediu um critério que ela "
                    "ainda não aplica (inclusive campo de tabela ligada), use antes ajustar_filtro nela; depois calcule "
                    "com preparar_consulta e agregacao, com o mesmo período e as mesmas filiais.")
        return txt


# --- persistência e encerramento ------------------------------------------------------------------

def _trilha_segura(trilha) -> list:
    return [x for x in trilha if re.fullmatch(r"[a-z_-]{1,40}:[a-z_-]{1,40}:[a-z_-]{1,40}", x)][-30:]


def registrar(ac: Acompanhamento, user, conversa, mensagem_id, situacao: str, motivo: str = "", incidente=None,
              espera_s: int = 0, tarefa_id=None) -> Objetivo:
    campos = {"exigencia": ac.exig or RESPOSTA, "origem": ac.origem or "acao", "situacao": situacao,
              "trilha": _trilha_segura(ac.trilha), "motivo": motivo[:300], "melhor_evidencia": ac.melhor,
              "incidente": incidente,
              "tarefa_id": tarefa_id,
              "lease_ate": timezone.now() + timedelta(seconds=espera_s) if espera_s else None}
    if ac.registro is not None:
        alteradas = Objetivo.objects.filter(pk=ac.registro.pk, execucao=ac.registro.execucao,
                                            situacao=SO.CONTINUAR).update(passos=ac.registro.passos + len(ac.trilha), execucao="", **{
            **campos, "incidente": incidente if incidente is not None else ac.registro.incidente})
        if alteradas != 1:
            from .agente import Interrompido
            raise Interrompido()
        ac.registro.refresh_from_db()
        return ac.registro
    return Objetivo.objects.create(usuario=user, conversa=conversa, mensagem_id=mensagem_id, passos=len(ac.trilha), **campos)


def encaminhar_manutencao(ac: Acompanhamento, causa: str, tabela: str = ""):
    """Capacidade ausente ou defeito sem exceção: diagnóstico só com códigos (exigência, causa, trilha)."""
    from iativa.manutencao import incidentes
    from iativa.manutencao.models import Categoria

    cond = {"exigencia": ac.exig or RESPOSTA, "causa": causa, "trilha": _trilha_segura(ac.trilha)}
    if re.fullmatch(r"[A-Z0-9]{3}", tabela or ""):
        cond["tabela"] = tabela.lower()
    return incidentes.registrar("agente", f"objetivo.{ac.exig or RESPOSTA}.{causa}", categoria=Categoria.CAPACIDADE,
                                condicoes=cond)


def resumo_tentativas(ac: Acompanhamento) -> str:
    from .agente import ROTULO_ACAO

    acoes = []
    for item in ac.trilha:
        a = item.split(":")[0]
        nome = ROTULO_ACAO.get(a, {"conversar": "responder sem executar"}.get(a, a.replace("_", " ")))
        if nome not in acoes:
            acoes.append(nome)
    return "; ".join(acoes[:6])


# --- continuação pelo worker ----------------------------------------------------------------------

def reivindicar() -> Objetivo | None:
    agora = timezone.now()
    lease = timedelta(seconds=180)
    fila = Objetivo.objects.filter(situacao=SO.CONTINUAR).filter(Q(lease_ate__isnull=True) | Q(lease_ate__lt=agora))
    for o in fila.order_by("id")[:5]:
        if o.continuacoes >= int(cfg()['continuacoes']):
            if Objetivo.objects.filter(pk=o.pk, execucao=o.execucao, situacao=SO.CONTINUAR).filter(
                    Q(lease_ate__isnull=True) | Q(lease_ate__lt=agora)).update(
                    situacao=SO.IMPEDIDO, motivo='Limite de continuações atingido.', execucao='', lease_ate=None):
                from iativa.chat.models import Mensagem, Papel
                from iativa.chat.conversas import pode_escrever
                if not superado(o) and pode_escrever(o.usuario, o.conversa):
                    Mensagem.objects.create(conversa=o.conversa, papel=Papel.IA,
                        texto='O pedido atingiu o limite de continuações e não foi concluído. Nenhuma nova tentativa foi iniciada.',
                        ficha={'tipo':'objetivo', 'objetivo':o.pk})
            continue
        nova = uuid.uuid4().hex
        n = Objetivo.objects.filter(pk=o.pk, situacao=SO.CONTINUAR, execucao=o.execucao).filter(
            Q(lease_ate__isnull=True) | Q(lease_ate__lt=agora)).update(
                execucao=nova, lease_ate=agora + lease, continuacoes=F('continuacoes') + 1)
        if n == 1:
            o.refresh_from_db()
            return o
    return None


def continuar_proximo() -> bool:
    """Worker: continua um objetivo preservado (limite de passos/tempo, falha passageira ou correção
    publicada). Permissões, critérios e evidências são refeitos pelo próprio ciclo."""
    o = reivindicar()
    if o is None:
        return False
    from . import agente

    token = o.execucao
    try:
        agente.continuar_objetivo(o)
    except Exception as exc:
        from iativa.manutencao import incidentes
        inc = incidentes.registrar('worker', 'objetivo.continuar', exc)
        atualizado = Objetivo.objects.filter(pk=o.pk, execucao=token, situacao=SO.CONTINUAR).update(
            situacao=SO.MANUTENCAO if inc and incidentes.elegivel(inc) else SO.IMPEDIDO,
            incidente=inc, motivo='Não foi possível executar a continuação.', execucao='', lease_ate=None)
        if atualizado and not superado(o):
            from iativa.chat.models import Mensagem, Papel
            Mensagem.objects.create(conversa=o.conversa, papel=Papel.IA,
                texto='A continuação encontrou uma falha técnica. O pedido não foi considerado concluído.',
                ficha={'tipo':'objetivo', 'objetivo':o.pk})
    return True


def superado(o: Objetivo) -> bool:
    """A pessoa já mandou outra mensagem depois do pedido: a conversa seguiu, não se responde fora de ordem."""
    return o.conversa.mensagens.filter(papel="usuario", pk__gt=o.mensagem_id).exists()


def retomar_apos_correcao(incidente_id: int) -> int:
    """Correção publicada e conferida: os pedidos que pararam por esse incidente voltam à fila UMA vez."""
    n = 0
    for o in Objetivo.objects.filter(situacao=SO.MANUTENCAO, incidente_id=incidente_id, retomado=False):
        n += Objetivo.objects.filter(pk=o.pk, situacao=SO.MANUTENCAO).update(
            situacao=SO.TAREFA if o.tarefa_id else SO.CONTINUAR, retomado=True, continuacoes=0,
            execucao="", lease_ate=None, motivo="Retomado após correção publicada.")
    return n


def sincronizar_tarefa(t):
    from .models import SituacaoTarefa as ST
    from iativa.manutencao.models import Incidente
    from iativa.manutencao.incidentes import elegivel

    alvos = Objetivo.objects.filter(tarefa=t, situacao__in=(SO.TAREFA, SO.AGUARDANDO))
    if t.cancelar_solicitado or t.situacao == ST.CANCELADA:
        alvos.update(situacao=SO.IMPEDIDO, motivo="Verificação cancelada.")
    elif t.situacao == ST.CONCLUIDA:
        cob = t.cobertura or {}
        completa = bool(cob.get('lista_completa')) and cob.get('sem_classificacao', 0) == 0
        for o in alvos:
            cumprido = completa and cumpre(o.exigencia, VERIFICACAO)
            Objetivo.objects.filter(pk=o.pk, situacao__in=(SO.TAREFA, SO.AGUARDANDO)).update(
                situacao=SO.CUMPRIDO if cumprido else SO.CONTINUAR if completa else SO.IMPEDIDO,
                melhor_evidencia=VERIFICACAO if completa else '', lease_ate=None,
                motivo='' if cumprido else 'Etapa concluída; ainda falta a entrega pedida.' if completa
                else 'Cobertura da verificação incompleta.')
    elif t.situacao == ST.AGUARDANDO:
        alvos.update(situacao=SO.AGUARDANDO, motivo='A verificação aguarda uma informação da pessoa.')
    elif t.situacao in (ST.PENDENTE, ST.EXECUTANDO):
        alvos.update(situacao=SO.TAREFA, motivo='')
    elif t.situacao in (ST.FALHOU, ST.BLOQUEADA, ST.LIMITE):
        inc = Incidente.objects.filter(pk=(t.consumo or {}).get('incidente')).first()
        if inc and elegivel(inc):
            alvos.filter(retomado=False).update(situacao=SO.MANUTENCAO, incidente=inc,
                                                motivo='A verificação não foi concluída.')
            alvos.filter(retomado=True).update(situacao=SO.IMPEDIDO, incidente=inc,
                                               motivo='A verificação falhou novamente após a correção.')
        else:
            alvos.update(situacao=SO.IMPEDIDO, incidente=inc, motivo='A verificação não foi concluída.')


def sincronizar_pendentes():
    from .models import TERMINAIS
    for o in Objetivo.objects.filter(situacao=SO.TAREFA, tarefa__situacao__in=TERMINAIS).select_related('tarefa')[:20]:
        sincronizar_tarefa(o.tarefa)


def encerrar_por_incidente(incidente_id: int, motivo: str) -> int:
    """A manutenção não corrigiu (não reproduziu, bloqueada): o pedido não fica pendurado em silêncio."""
    from iativa.chat.models import Mensagem, Papel

    n = 0
    for o in Objetivo.objects.filter(situacao=SO.MANUTENCAO, incidente_id=incidente_id):
        if Objetivo.objects.filter(pk=o.pk, situacao=SO.MANUTENCAO).update(situacao=SO.IMPEDIDO, motivo=motivo[:300]) != 1:
            continue
        n += 1
        if not superado(o):
            Mensagem.objects.create(conversa=o.conversa, papel=Papel.IA, ficha={"tipo": "objetivo", "objetivo": o.pk},
                                    texto=(f"Sobre o pedido que ficou pendente: a manutenção automática não conseguiu "
                                           f"corrigir (diagnóstico nº {incidente_id}); ficou para análise de uma pessoa da TI. "
                                           f"{FALTA.get(o.exigencia, FALTA[RESPOSTA])}."))
    return n
