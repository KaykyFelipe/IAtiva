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
from . import andamento, investigacao, prazo
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
# M41: pedido explícito de mudar o critério de uma consulta ("ajuste o filtro", "tire os cancelados", "volte como era").
PEDE_AJUSTE_RE = re.compile(r"filtr|ajust|crit[eé]rio|regra|consider|inclu|exclu|\btir[ae]|remov|\bvolt|\bmud|\btroc|desfa",
                            re.I)
PARAMETROS_APRENDIDOS = ("filial-analise", "periodo-analise", "texto-busca", "filtro-campo", "agregacao")


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
    return json.dumps([d.acao, d.objetivo, sorted(d.parametros), d.alvo, d.nota, d.consulta, d.regra, d.investigacao],
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


def conversar(user, c, texto: str, provedor=None, mensagem_usuario_id=None, interrompido=None, acomp=None,
              observacoes=None) -> Saida:
    objetivos = objetivos_para(user)
    administrador = pode_aprender(user)
    if not objetivos and not administrador:
        registrar("acesso_negado", "agente", usuario=user, estado_novo="dominio", resultado="negado")
        return Saida("Seu usuário ainda não tem acesso a consultas. Fale com o administrador.")

    # Parâmetros das consultas aprendidas valem mesmo antes de existir a primeira (M24): a IA pode
    # pedir "some X" e criar a consulta já com o cálculo.
    codigos_par = sorted({cod for o in objetivos for cod in o.parametros} | set(PARAMETROS_APRENDIDOS))
    defs = orq._definicoes(codigos_par)
    atual = _ativa(user, c)
    direta = _resposta_direta(user, atual, texto, defs) if acomp is None else None
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
    aprendidos = _aprendidos(administrador)
    andamento.avisar("Lendo o pedido e o histórico da conversa")
    from .models import SituacaoTarefa, Tarefa

    aguardando = Tarefa.objects.filter(usuario=user, conversa=c, situacao=SituacaoTarefa.AGUARDANDO).order_by(
        "-id").first()
    base_lista = (aguardando.solicitacao_base if aguardando is not None
                  else (_lista_vista(user, c) if INVESTIGAR_RE.search(_norm(texto)) else None))
    lista = investigacao.contexto_para_modelo(user, base_lista) if base_lista is not None else None
    if lista is not None and aguardando is not None:
        lista = {**lista, "pergunta_pendente": aguardando.motivo,
                 "instrucao": "A mensagem responde à pergunta pendente da verificação: use investigar_lista com o que "
                              "ela disse para retomar a mesma verificação."}
    entrada = EntradaAgente(
        lista_anterior=lista,
        mensagem=texto, hoje=timezone.localdate().isoformat(), historico=_historico(c, mensagem_usuario_id),
        objetivos=_catalogo(user, objetivos, defs), solicitacao_atual=_estado(user, atual, defs),
        notas=_notas(user, texto), aprendidos=aprendidos, trechos=trechos, administrador=administrador,
        pendencias=_pendencias_contexto(user),
    )
    try:
        provedor = provedor or obter_provedor(usuario=user)
    except ImproperlyConfigured:
        return Saida("A conexão de IA precisa de configuração. Confira Conexões IA e a liberação de privacidade do IAtiva.",
                     ficha={"tipo": "falha_modelo"})
    # Ciclo: agir → observar → corrigir (falha recuperável) ou concluir. Limites configuráveis.
    # O prazo é total: conferido antes de cada chamada ao modelo e antes de cada ação; as chamadas
    # e as consultas usam no máximo o tempo que resta (M24).
    cfg = _ciclo()
    t_norm = _norm(texto)
    from .objetivos import _verificar_pessoas
    pede_cobertura = _verificar_pessoas(t_norm)
    cobertura = base_lista if (lista is not None and pede_cobertura) else None
    with prazo.definir(float(cfg["tempo_s"])):
        return _ciclo_agente(user, c, texto, provedor, interrompido, entrada, objetivos, codigos_par, defs, atual,
                             administrador, cfg, cobertura, pede_cobertura, mensagem_id=mensagem_usuario_id,
                             acomp=acomp, observacoes=observacoes)


def _aprendidos(administrador) -> list[dict]:
    if not administrador:
        return []
    from .aprendizado import descrever_filtros

    return [{"codigo": cm.codigo, "nome": cm.nome, "tabela": cm.tabela, "situacao": cm.get_situacao_display(),
             "filtros": descrever_filtros(cm), "pode_voltar_filtro": bool(cm.spec.get("anteriores"))}
            for cm in ConsultaModelo.objects.exclude(situacao=SituacaoModelo.REMOVIDA)]


def _consulta_recente(user, c):
    """Consulta da última solicitação desta conversa (código e filtros), para orientar o próximo passo."""
    s = _ultima(user, c)
    if s is None or not s.objetivo:
        return None
    cm = ConsultaModelo.objects.filter(codigo=s.objetivo).first()
    if cm is None:
        return {"codigo": s.objetivo}
    from .aprendizado import descrever_filtros

    return {"codigo": cm.codigo, "tabela": cm.tabela, "filtros": descrever_filtros(cm)}


SEM_TEMPO = "O tempo total para responder esta mensagem acabou antes de eu concluir."


def _ciclo_agente(user, c, texto, provedor, interrompido, entrada, objetivos, codigos_par, defs, atual,
                  administrador, cfg, cobertura=None, verificar=False, mensagem_id=None, acomp=None,
                  observacoes=None) -> Saida:
    """Ciclo com evidência de cumprimento (M38). Cada passo: decidir → agir → medir o que foi entregue
    contra o que o pedido exige (`objetivos.py`). Consulta que deu certo não encerra uma verificação;
    texto que só anuncia ou repete não é progresso; sem progresso, para e encaminha em vez de insistir."""
    from . import diagnostico, historico_funcional
    from . import objetivos as coord

    ac = acomp or coord.Acompanhamento(texto)
    feitas, observacoes, saida = set(), list(observacoes or []), None
    previa = None  # etapa intermediária concluída (ex.: a lista) que a próxima etapa completa
    causa = "passos"
    for _ in range(max(1, int(cfg["passos"]))):
        if prazo.esgotado():
            causa = "prazo"
            break
        entrada_passo = entrada if not observacoes else replace(
            entrada, solicitacao_atual=_estado(user, atual, defs), observacoes=[o.para_modelo() for o in observacoes])
        andamento.avisar("Decidindo o que fazer" if not observacoes else
                         "Ajustando o caminho: " + observacoes[-1].detalhe[:110])
        try:
            d = _interpretar(provedor, entrada_passo, [o.codigo for o in objetivos], codigos_par)
        except prazo.PrazoEsgotado:
            causa = "prazo"
            break
        except FalhaModelo as exc:
            if saida is not None or prazo.esgotado() or ac.trilha or ac.registro is not None:
                if not ac.trilha:
                    ac.anotar('continuacao', 'modelo_falhou')
                causa = "prazo" if prazo.esgotado() else "modelo_falhou"
                break  # a correção não veio (ou o tempo acabou): decide abaixo se continua depois
            registrar("modelo_falhou", "agente", usuario=user, estado_novo=provedor.nome.replace('-', '_'), resultado="erro")
            if provedor.nome in ("claude-code", "chatgpt"):
                return Saida(str(exc), ficha={"tipo": "falha_modelo"})
            if exc.temporaria:
                return Saida("A IA (Gemini) está sobrecarregada ou no limite de uso do plano gratuito neste momento. "
                             "Tente de novo em cerca de um minuto, com a mesma pergunta.", ficha={"tipo": "falha_modelo"})
            return Saida("Não consegui processar sua mensagem agora. Pode repetir ou escrever de outro jeito?",
                         ficha={"tipo": "falha_modelo"})
        if interrompido is not None and interrompido():
            raise Interrompido  # antes de qualquer ação (e de consultar o ERP)
        if prazo.esgotado():
            causa = "prazo"
            break  # o modelo respondeu tarde: a ação não é executada com o prazo vencido
        if diagnostico.solicitado(texto) and diagnostico.reutilizavel(texto) and d.acao == 'conversar':
            anterior = _ultima(user, c)
            if (anterior is not None and anterior.objetivo in {o.codigo for o in objetivos}
                    and OBJETIVOS[anterior.objetivo].modelo_id):
                from iativa.query.servico import repetir
                atual = repetir(user, anterior, conversa=c)
                d = replace(d, acao='executar_consulta', objetivo=atual.objetivo, resposta='')
        if historico_funcional.solicitado(texto) and d.acao in (
                "conversar", "preparar_consulta", "executar_consulta", "aprender_tabela", "criar_consulta", "historico_cargo"):
            # Histórico funcional exige encadear fontes: listar matrículas não conclui esse pedido.
            nome = historico_funcional.nome_contexto(user, c, texto, d)
            d = replace(d, acao="historico_cargo", alvo=nome)
        ac.definir(d)
        # Texto que só anuncia uma ação, ou repete a resposta anterior para outra pergunta, não é progresso.
        sem_acao = coord.texto_sem_acao(d, c, mensagem_id, texto) if cobertura is None else ""
        if sem_acao:
            ac.anotar(d.acao, sem_acao)
            registrar("interpretacao", "agente", usuario=user, estado_novo=sem_acao, resultado="erro")
            if ac.pode_cutucar():
                ac.cutucadas += 1
                observacoes.append(Observacao(d.acao, sem_acao, coord.TEXTO_OBS[sem_acao]))
                andamento.avisar("A resposta só anunciava a ação; pedindo a execução" if sem_acao == coord.ANUNCIO
                                 else "A resposta repetia a anterior; mudando de estratégia")
                continue
            causa = sem_acao
            break
        assinatura = _assinatura(d)
        if assinatura in feitas:
            ac.anotar(d.acao, "acao_repetida")
            if ac.pode_cutucar():
                # M40: repetir o que já foi feito não é progresso; antes de desistir, pede outra estratégia.
                ac.cutucadas += 1
                observacoes.append(Observacao(d.acao, "acao_repetida", coord.TEXTO_REPETIDA_ACAO))
                andamento.avisar("A mesma ação já foi feita; mudando de estratégia")
                continue
            causa = "acao_repetida"
            break  # a mesma ação com os mesmos dados já foi feita: não repete
        feitas.add(assinatura)
        registrar("interpretacao", "agente", usuario=user, estado_novo=d.acao, entidade_id=atual.pk if atual else None,
                  linhas=len(d.parametros))
        reduz = _reduz_escopo(d, cobertura)
        saida = reduz or _agir(user, c, d, atual, defs, texto, provedor, entrada_passo, objetivos, administrador,
                               interrompido=interrompido)
        if ((d.acao == 'investigar_divergencia' or diagnostico.solicitado(texto)) and saida.solicitacao is not None
                and (getattr(saida.resultado, 'ok', False) or getattr(saida.resultado, 'consolidado', False))):
            andamento.avisar('A soma inicial não conclui o pedido; investigando as causas da divergência')
            try:
                saida.resultado = diagnostico.investigar(user, saida.solicitacao, saida.resultado, interrompido)
                saida.texto = 'Investiguei a divergência. As verificações, evidências e limitações estão na resposta abaixo.'
                saida.ficha = {**saida.ficha, 'tipo': 'diagnostico',
                               'investigacao_completa': saida.resultado.completo, 'conclusao_validada': False}
            except PermissaoNegada:
                saida = Saida('A investigação foi interrompida por uma restrição de acesso.',
                              {**saida.ficha, 'tipo': 'bloqueada'})
            except (ValidationError, prazo.PrazoEsgotado):
                from .diagnostico import Diagnostico, Bloco
                saida.resultado = Diagnostico([Bloco('Consulta inicial', saida.resultado)],
                    'A investigação não foi concluída por indisponibilidade de metadados ou prazo. '
                    'A consulta inicial não comprova a causa da divergência.', completo=False)
                saida.texto = 'A investigação ficou incompleta; não apresentei a soma inicial como conclusão.'
                saida.ficha = {**saida.ficha, 'tipo': 'diagnostico', 'investigacao_completa': False}
            ev = coord.evidencia(d, saida)
            ac.anotar(d.acao, "ok" if ev else "incompleta", ev)
            if not ev and getattr(saida.resultado, 'incidentes', ()):
                from iativa.manutencao.models import Incidente
                from iativa.manutencao.incidentes import elegivel
                inc = next((i for i in Incidente.objects.filter(pk__in=saida.resultado.incidentes)
                            if elegivel(i)), None)
                if inc is not None and mensagem_id is not None:
                    from .models import SituacaoObjetivo as SO
                    estado = SO.IMPEDIDO if ac.registro and ac.registro.retomado else SO.MANUTENCAO
                    o = coord.registrar(ac, user, c, mensagem_id, estado,
                                         'Falha técnica na investigação.', incidente=inc)
                    saida.ficha = {**saida.ficha, 'objetivo': o.pk}
                    saida.texto = ('A investigação encontrou uma falha técnica. O diagnóstico foi encaminhado à '
                                   'manutenção; as etapas incompletas não foram tratadas como conclusão.'
                                   + (' Após uma correção publicada, retomarei este pedido.' if estado == SO.MANUTENCAO else ''))
                    return saida
            # Investigação parcial mostra o que conseguiu, marcada como incompleta (nunca como conclusão).
            return _fechar(ac, user, c, mensagem_id, saida, ev or coord.INVESTIGACAO, parcial=not ev)
        ev = coord.evidencia(d, saida)  # o que ESTA ação entregou (antes de juntar com a etapa anterior)
        if previa is not None:
            saida = _juntar(previa, saida)
        ac.anotar(d.acao, saida.observacao.situacao if saida.observacao else ("ok" if ev else "sem_resultado"), ev)
        if d.acao == "lembrar":
            ac.memoria_feita = True
            if ac.entregue:  # a resposta já estava pronta; a memória era o que faltava
                return _fechar(ac, user, c, mensagem_id, saida, ac.entregue)
        if (ac.cumprido(ev) and coord.pede_memoria(texto) and not ac.memoria_feita and d.acao != "lembrar"
                and ac.pode_cutucar()):
            # M40: "me dê o valor e guarde isso": a resposta saiu, mas o ensinamento ainda não foi guardado.
            ac.cutucadas += 1
            ac.entregue, previa = ev, saida
            observacoes.append(Observacao(d.acao, coord.ETAPA, coord.FALTA_MEMORIA))
            andamento.avisar("Resposta pronta; agora guardando o que você ensinou")
            continue
        if ac.partes_faltam > 0 and ac.entregue and ev and ev not in (coord.PERGUNTA, coord.BLOQUEIO, coord.DELEGADA,
                                                                       coord.ACAO):
            ac.partes_faltam -= 1  # M41: mais uma das perguntas da mensagem respondida
        elif ac.cumprido(ev) and ac.partes_faltam < 0 and d.acao != "lembrar":
            ac.partes_faltam = coord.partes(texto) - 1
        if (ac.partes_faltam > 0 and (ac.cumprido(ev) or ac.entregue) and ac.pode_cutucar()
                and ev not in (coord.PERGUNTA, coord.BLOQUEIO, coord.DELEGADA)):
            # M41: "quantos entraram? e quantos saíram?": a primeira resposta saiu; falta a outra pergunta.
            ac.cutucadas += 1
            ac.entregue = ac.entregue or ev
            previa = saida
            observacoes.append(Observacao(d.acao, coord.ETAPA, coord.falta_parte(
                texto, coord.partes(texto) - ac.partes_faltam)))
            andamento.avisar("Uma pergunta respondida; agora a próxima")
            continue
        if ac.entregue and ev and ev not in (coord.PERGUNTA, coord.BLOQUEIO, coord.DELEGADA, coord.ACAO):
            return _fechar(ac, user, c, mensagem_id, saida, ac.entregue)
        if ac.cumprido(ev) or ev in (coord.PERGUNTA, coord.BLOQUEIO, coord.DELEGADA):
            return _fechar(ac, user, c, mensagem_id, saida, ev)
        if saida.observacao is not None and saida.observacao.recuperavel:
            observacoes.append(saida.observacao)
            atual = _ativa(user, c)
            continue
        if ev:
            # Sucesso intermediário: a ação deu certo, mas entregou menos do que o pedido exige.
            if saida.resultado is not None or saida.ficha.get("tipo"):
                previa = saida
            if ac.exig == coord.VERIFICACAO:
                base = _lista_vista(user, c)
                lista = investigacao.contexto_para_modelo(user, base) if base is not None else None
                if lista is not None:
                    cobertura = base
                    entrada = replace(entrada, lista_anterior=lista)
            if d.acao in ("aprender_tabela", "criar_consulta", "ajustar_filtro", "voltar_filtro"):
                # M40: a consulta criada/ajustada neste passo precisa estar entre as que o modelo pode escolher.
                objetivos = objetivos_para(user)
                codigos_par = sorted({cod for o in objetivos for cod in o.parametros} | set(PARAMETROS_APRENDIDOS))
                defs = orq._definicoes(codigos_par)
                entrada = replace(entrada, objetivos=_catalogo(user, objetivos, defs),
                                  aprendidos=_aprendidos(administrador))
            observacoes.append(Observacao(d.acao, coord.ETAPA, ac.falta_obs(_consulta_recente(user, c))))
            andamento.avisar("Etapa concluída; ainda falta parte do pedido" if ac.exig != coord.VERIFICACAO
                             else "Lista pronta; agora vou verificar cada pessoa")
            atual = _ativa(user, c)
            continue
        causa = saida.observacao.situacao if saida.observacao is not None else "sem_resultado"
        break
    return _encerrar_sem_cumprir(ac, user, c, mensagem_id, saida, previa, causa, observacoes)


PASSAGEIRAS = ("prazo", "passos", "modelo_falhou", TEMPO_ESGOTADO, FALHA_TRANSITORIA)


def _fechar(ac, user, c, mensagem_id, saida, ev, parcial=False) -> Saida:
    """Pedido encerrado por evidência (cumprido), por pergunta à pessoa ou por impedimento real."""
    from . import objetivos as coord
    from .models import SituacaoObjetivo as SO

    situacao = (SO.TAREFA if ev == coord.DELEGADA else SO.AGUARDANDO if ev == coord.PERGUNTA else SO.IMPEDIDO if (ev == coord.BLOQUEIO or parcial)
                else SO.CUMPRIDO)
    if mensagem_id is not None and (ac.registro is not None or len(ac.trilha) > 1 or situacao != SO.CUMPRIDO):
        o = coord.registrar(ac, user, c, mensagem_id, situacao,
                            "investigação incompleta" if parcial else ("sem permissão" if ev == coord.BLOQUEIO else ""),
                            tarefa_id=saida.ficha.get('tarefa') if ev == coord.DELEGADA else None)
        saida.ficha = {**saida.ficha, "objetivo": o.pk}
    return saida


def _encerrar_sem_cumprir(ac, user, c, mensagem_id, saida, previa, causa, observacoes) -> Saida:
    """O ciclo parou sem a evidência exigida. Três saídas honestas, nunca "pronto":
    continuar em segundo plano (limite/falha passageira com trabalho andando), encaminhar à manutenção
    (sem progresso: capacidade ausente ou defeito sem exceção) ou impedimento real explicado."""
    from . import objetivos as coord
    from .models import SituacaoObjetivo as SO

    if saida is None and not ac.trilha:
        registrar("modelo_falhou", "agente", usuario=user, estado_novo="prazo_esgotado", resultado="erro")
        return Saida(SEM_TEMPO + " Nada foi consultado. Tente de novo; se repetir, a IA está lenta agora.",
                     ficha={"tipo": "falha_modelo"}, observacao=Observacao("", TEMPO_ESGOTADO, "prazo total"))
    ficha = dict(saida.ficha) if saida is not None else {}
    texto = (saida.texto if saida is not None else "").strip()
    if prazo.esgotado() and saida is not None and saida.observacao is not None and saida.observacao.recuperavel:
        texto = f"{texto} {SEM_TEMPO}".strip()
    tentativas = [o for o in observacoes if o.situacao in RECUPERAVEIS]
    if len(tentativas) > 1:
        # Limite atingido sem resolver: diz o que foi tentado, em vez de uma recusa genérica.
        texto = (f"{texto} Tentei {len(tentativas)} caminhos nesta mensagem ("
                 + "; ".join(sorted({ROTULO_ACAO.get(o.acao, o.acao) for o in tentativas}))
                 + ") e parei no limite de tentativas.").strip()
        ficha["tentativas"] = len(tentativas)
    exig = ac.exig or coord.RESPOSTA
    falta = coord.FALTA[exig]
    if exig == coord.VERIFICACAO:
        ficha["verificacao_pendente"] = True
    registro = ac.registro
    feitas = registro.continuacoes if registro is not None else 0
    andando = not ac.estagnado() or causa in (TEMPO_ESGOTADO, FALHA_TRANSITORIA, "modelo_falhou", "prazo")
    if (causa in PASSAGEIRAS and andando and mensagem_id is not None and feitas < int(coord.cfg()["continuacoes"])
            and not (registro is not None and registro.retomado and causa == "passos")):
        o = coord.registrar(ac, user, c, mensagem_id, SO.CONTINUAR, f"continua: {causa}",
                            espera_s=int(coord.cfg()["espera_s"]))
        final = Saida(f"{texto}\n\n{falta}. Continuo em segundo plano e respondo aqui, sem você precisar pedir de novo."
                      .strip(), {**ficha, "tipo": ficha.get("tipo") or "objetivo", "objetivo": o.pk})
        registrar("modelo_falhou", "agente", usuario=user, estado_novo="objetivo_continua", resultado="erro")
    elif causa in PASSAGEIRAS:
        # Falha passageira que persistiu: não é defeito de código, não vai para a manutenção.
        if mensagem_id is not None:
            o = coord.registrar(ac, user, c, mensagem_id, SO.IMPEDIDO, f"falha passageira persistente: {causa}")
            ficha["objetivo"] = o.pk
        final = Saida(f"{texto}\n\n{falta}. O ERP ou a IA não responderam a tempo mesmo depois de eu tentar de novo; "
                      "nada ficou pela metade. Tente outra vez em alguns minutos.".strip(), ficha)
    else:
        ultima = _ultima(user, c)
        tabela = ""
        if ultima is not None and ultima.objetivo in OBJETIVOS and OBJETIVOS[ultima.objetivo].modelo_id:
            tabela = OBJETIVOS[ultima.objetivo].tabelas[0]
        inc = coord.encaminhar_manutencao(ac, causa, tabela)
        retomado = registro is not None and registro.retomado
        situacao = SO.MANUTENCAO if (inc is not None and mensagem_id is not None and not retomado) else SO.IMPEDIDO
        if mensagem_id is not None:
            o = coord.registrar(ac, user, c, mensagem_id, situacao, f"sem evidência: {causa}", incidente=inc)
            ficha["objetivo"] = o.pk
        tentei = coord.resumo_tentativas(ac)
        aviso = (f" Registrei o diagnóstico técnico nº {inc.pk} para a manutenção (só códigos do que foi tentado, sem "
                 "dados da consulta). Se uma correção for publicada, refaço este pedido e respondo aqui."
                 if situacao == SO.MANUTENCAO else
                 " Isso já tinha sido encaminhado à manutenção e voltou a acontecer; ficou para análise de uma pessoa da TI."
                 if retomado else "")
        final = Saida(f"{texto}\n\n{falta}." + (f" Tentei: {tentei}." if tentei else "")
                      + " Não vou repetir a mesma tentativa nem dizer que está pronto." + aviso, ficha)
        registrar("modelo_falhou", "agente", usuario=user, estado_novo=f"objetivo_{causa}"[:40], resultado="erro")
    final.texto = final.texto.strip()
    final.solicitacao = saida.solicitacao if saida is not None else None
    final.observacao = saida.observacao if saida is not None else None
    if saida is not None and saida.resultado is not None:
        final.resultado = saida.resultado
    return _juntar(previa, final) if previa is not None and previa is not saida else final


def continuar_objetivo(o):
    """Worker: continua o pedido preservado (M38). Refaz o ciclo com orçamento novo a partir do texto
    original (que está na mensagem da pessoa); permissões e critérios são reconferidos pelo ciclo.
    O resultado em memória não é gravado: a mensagem diz como vê-lo de novo."""
    from django.db import transaction

    from iativa.chat import conversas
    from iativa.chat.models import Mensagem, Papel

    from . import objetivos as coord
    from .models import Objetivo, SituacaoObjetivo as SO

    with transaction.atomic():
        user, c = o.usuario, o.conversa
        token = o.execucao

        def superado():
            return coord.superado(o) or not conversas.pode_escrever(user, c)

        def interrompido():
            return superado() or not Objetivo.objects.filter(pk=o.pk, execucao=token, situacao=SO.CONTINUAR).exists()

        def descartar(saida=None):
            if saida is not None and saida.ficha.get('tipo') == 'tarefa':
                from .tarefas import cancelar
                cancelar(user, saida.ficha['tarefa'])
            Objetivo.objects.filter(pk=o.pk, execucao__in=(token, '')).update(
                situacao=SO.SUPERADO, motivo='A conversa seguiu ou o acesso mudou.', execucao='', lease_ate=None)

        if interrompido():
            if superado():
                descartar()
            return None
        if o.continuacoes > int(coord.cfg()['continuacoes']):
            Objetivo.objects.filter(pk=o.pk, execucao=token).update(
                situacao=SO.IMPEDIDO, motivo='Limite de continuações atingido.', execucao='', lease_ate=None)
            return Mensagem.objects.create(conversa=c, papel=Papel.IA,
                texto='O pedido atingiu o limite de continuações e não foi concluído. Nenhuma nova tentativa foi iniciada.',
                ficha={'tipo':'objetivo', 'objetivo':o.pk})
        ac = coord.Acompanhamento(o.mensagem.texto, exig=o.exigencia, origem=o.origem, registro=o)
        feito = ", ".join(o.trilha[-6:]) or "nada concluído"
        obs = [Observacao("continuacao", "continuacao",
                          f"Continuação automática do MESMO pedido (a pessoa não mandou nova mensagem). Já tentado "
                          f"(ação:situação:evidência): {feito}. {coord.FALTA.get(o.exigencia, '')}: {coord.COMO.get(o.exigencia, '')}.")]
        try:
            saida = conversar(user, c, o.mensagem.texto, mensagem_usuario_id=o.mensagem_id, acomp=ac,
                              observacoes=obs, interrompido=interrompido)
        except Interrompido:
            if superado():
                descartar()
            return None
        if superado():
            descartar(saida)
            return None
        # Saídas anteriores à primeira decisão também encerram a concessão de execução.
        if o.execucao == token:
            Objetivo.objects.filter(pk=o.pk, execucao=token).update(
                situacao=SO.IMPEDIDO, motivo='O atendimento não conseguiu iniciar a continuação.',
                execucao='', lease_ate=None)
        prefixo = ("Depois da correção publicada, refiz o seu pedido. " if o.retomado
                   else "Continuando o seu pedido, sem você precisar pedir de novo. ")
        texto = prefixo + saida.texto
        if saida.resultado is not None and saida.solicitacao is not None:
            texto += "\n\nOs dados não ficam guardados fora da tela: use “Executar de novo” para vê-los."
        return Mensagem.objects.create(conversa=c, papel=Papel.IA, texto=texto[:4000], ficha=saida.ficha,
                                       solicitacao=saida.solicitacao)


# M25: pedidos sobre a lista inteira geram obrigação de cobertura verificada pelo backend.
INVESTIGAR_RE = re.compile(r"\b(todos|todas|cada|demais|lista|confir|verific|confer|chec|algum|alguma|readmit|"
                           r"vinculo|mesmo)\w*")
COBERTURA_RE = re.compile(r"\b(todos|todas|cada um|cada uma|demais|a lista|da lista|na lista|essa lista|esses|"
                          r"essas|eles|elas|o resto)\b")


VERIFICAR_RE = re.compile(r"\b(verifi|confir|confer|chec|investig|descubr)\w*")
REFERENCIA_RE = re.compile(r"\b(deles|delas|eles|elas|esses|essas|cada|todos|todas|lista|demitid|desligad|admitid|"
                           r"readmit|matricula|vinculo|funcionari|pessoas)\w*")


def _juntar(previa: "Saida", saida: "Saida") -> "Saida":
    """A resposta final inclui a etapa anterior (ex.: a lista que foi mostrada) e o passo seguinte."""
    if saida.resultado is None:
        saida.resultado = previa.resultado
    if previa.texto and previa.texto not in saida.texto:
        saida.texto = f"{previa.texto}\n\n{saida.texto}".strip()
    if saida.solicitacao is None:
        saida.solicitacao = previa.solicitacao
    return saida


def _lista_vista(user, c):
    """Última lista concluída nesta conversa (a que a pessoa está vendo)."""
    s = c.solicitacoes.filter(usuario=user, estado=E.CONCLUIDA).exclude(objetivo="").order_by("-id").first()
    return s if s is not None and s.objetivo in OBJETIVOS and OBJETIVOS[s.objetivo].modelo_id else None


def _reduz_escopo(d, cobertura) -> "Saida | None":
    """Pedido cobre a lista inteira, mas a ação consulta só um exemplo (busca por nome/filtro) ou só
    responde sem agir: devolve ao modelo, sem executar, para investigar todos."""
    if cobertura is None:
        return None
    exemplo = d.acao == "preparar_consulta" and any(cod in (BUSCA, "filtro-campo") for cod, _ in d.parametros)
    adia = d.acao == "conversar" and not d.resposta.strip().endswith("?")
    if not (exemplo or adia):
        return None
    motivo = ("O pedido vale para TODOS os integrantes da lista anterior; consultar só o exemplo (ou deixar para "
              "depois) não atende. Use investigar_lista com os campos de lista_anterior.")
    return Saida("Esse pedido vale para a lista inteira; não consultei só o exemplo.",
                 observacao=Observacao(d.acao, ERRO_PARAMETROS, motivo))


ROTULO_ACAO = {"investigar_lista": "verificar a lista inteira", "preparar_consulta": "consultar", "executar_consulta": "executar a consulta",
               "aprender_tabela": "procurar a tabela no dicionário", "criar_consulta": "criar uma consulta nova",
               "lembrar": "guardar na memória"}


def _agir(user, c, d, atual, defs, texto, provedor, entrada, objetivos, administrador, interrompido=None) -> Saida:
    """Executa UMA ação já autorizada e devolve a saída com a observação do que aconteceu."""
    ficha = {"provedor": provedor.nome, "opcoes": list(d.opcoes)}

    acao = d.acao
    if acao == "historico_cargo":
        from . import historico_funcional
        from .periodo import periodo_da_mensagem

        nome = d.alvo or historico_funcional.nome_contexto(user, c, texto, d)
        if not nome:
            return Saida("De qual funcionário você quer o histórico de cargos e funções? Informe o nome.", ficha)
        try:
            resultado = historico_funcional.consultar(user, c, nome, periodo_da_mensagem(texto, timezone.localdate()),
                interrompido=interrompido, filial=next((v for k, v in d.parametros if k == 'filial-analise'), None))
        except PermissaoNegada as exc:
            return Saida(str(exc), ficha, observacao=Observacao(acao, SEM_PERMISSAO, "histórico não autorizado"))
        except ValidationError as exc:
            return Saida(" ".join(exc.messages), ficha)
        except prazo.PrazoEsgotado:
            return Saida("O prazo terminou antes de concluir o histórico. A busca de matrículas não foi tratada como resposta final.", ficha)
        return Saida("Consultei os vínculos, o histórico funcional e as descrições disponíveis.",
                     {**ficha, "tipo": "historico_cargo", "busca": nome}, resultado=resultado)
    if acao in ("ajustar_filtro", "voltar_filtro"):
        from .aprendizado import pode_ajustar

        if not pode_ajustar(user):
            return Saida("Seu usuário não pode ajustar filtros de consultas.", ficha,
                         observacao=Observacao(acao, SEM_PERMISSAO, "ajuste não autorizado"))
        if not PEDE_AJUSTE_RE.search(texto or ""):
            # M41: a consulta é compartilhada; mudar o filtro sem a pessoa pedir altera a resposta de todos.
            msg = ("Ajustar ou voltar filtro só quando a pessoa pede. Ela não pediu: não mude a consulta "
                   "compartilhada. Use outra consulta que já responda (veja os objetivos) ou crie uma nova "
                   "(aprender_tabela / criar_consulta) com os filtros que esta pergunta precisa.")
            return Saida(msg, ficha, observacao=Observacao(acao, ERRO_PARAMETROS, msg))
        return _ajustar_filtro(user, d, ficha)
    if acao == "remover_aprendizado" and not administrador:
        return Saida("Essa ação é só para administradores.", ficha)
    if acao in ("preparar_consulta", "investigar_divergencia"):
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
    if acao == "investigar_lista":
        return _investigar(user, c, d, texto, ficha)
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
        if b.definicao.codigo == "agregacao":
            continue  # o cálculo vale só para o pedido em que foi feito: "quais os nomes?" volta a listar
        if falhou and b.definicao.codigo == "filtro-campo":
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
    andamento.avisar(f"Preparando a consulta “{OBJETIVOS[objetivo].nome}”")
    pares = list(d.parametros)
    if any(cod == "agregacao" for cod, _ in pares) and "agregacao" not in OBJETIVOS[objetivo].parametros:
        # Consulta fixa não faz cálculo sob pedido: em vez de devolver a lista calada, a IA é avisada
        # e pode criar uma consulta aprendida com o cálculo.
        msg = (f"A consulta “{OBJETIVOS[objetivo].nome}” não faz cálculos sob pedido. Para somar, contar ou agrupar, "
               "crie uma consulta aprendida sobre a tabela (aprender_tabela).")
        return Saida(msg, ficha, atual, observacao=Observacao("preparar_consulta", ERRO_PARAMETROS, msg))
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
        andamento.avisar("A consulta não respondeu a tempo; tentando de novo")
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
    andamento.avisar("Procurando no dicionário do Protheus a tabela certa")
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
        andamento.avisar("Montando a consulta nova com os campos de " + ", ".join(t["tabela"] for t in tabelas))
        entrada2 = replace(entrada, tabelas_para_aprender=tabelas)
        codigos_par = sorted({p["codigo"] for o in entrada.objetivos for p in o.get("parametros", [])}
                             | set(PARAMETROS_APRENDIDOS))
        d2 = _interpretar(provedor, entrada2, [o["codigo"] for o in entrada.objetivos], codigos_par)
        if d2.acao != "criar_consulta" or d2.consulta is None:
            motivo.append("as tabelas encontradas (" + ", ".join(t["tabela"] for t in tabelas) + ") não serviram")
            return None
        d, consulta = d2, d2.consulta
    consulta = {**consulta, "campo_data": consulta.get("campo_data", "").upper(),
                "campo_busca": consulta.get("campo_busca", "").upper()}
    andamento.avisar(f"Criando a consulta “{consulta.get('nome', '')}” ({consulta.get('tabela', '')})")
    try:
        cm = aprendizado.criar_automatico(user, consulta)
    except (ValidationError, PermissaoNegada) as exc:
        registrar("modelo_falhou", "agente", usuario=user, estado_novo="aprendizado_automatico", resultado="erro")
        detalhe = "; ".join(getattr(exc, "messages", None) or [str(exc)])
        motivo.append(f"consulta sobre {consulta.get('tabela', '')} recusada: {detalhe}"[:300])
        return None
    registrar("interpretacao", "agente", usuario=user, estado_novo="aprendeu_sozinho", entidade_id=cm.pk)
    if consulta.get("filtros") and not cm.spec.get("filtros_fixos"):
        # M40: a consulta nasce com os filtros da definição (ex.: faturamento só com TES que gera duplicata).
        try:
            cm = aprendizado.ajustar_filtros(user, cm, consulta["filtros"], [])
        except (ValidationError, PermissaoNegada) as exc:
            detalhe = "; ".join(getattr(exc, "messages", None) or [str(exc)])
            motivo.append(f"filtro da consulta nova recusado: {detalhe}"[:300])
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


def _ajustar_filtro(user, d, ficha) -> Saida:
    """M40: muda (ou desfaz) os filtros fixos de uma consulta aprendida a pedido da pessoa."""
    from .aprendizado import ajustar_filtros, descrever_filtros, voltar_filtros

    achados = _achar_modelo(d.alvo)
    if not achados:
        nomes = "; ".join(f"“{m.nome}”" for m in ConsultaModelo.objects.filter(situacao=SituacaoModelo.APROVADA))
        return Saida("Qual consulta devo ajustar?" + (f" As que existem são: {nomes}." if nomes else ""), ficha)
    if len(achados) > 1:
        ficha["opcoes"] = [m.nome for m in achados[:6]]
        return Saida("Encontrei mais de uma. Qual delas?", ficha)
    cm = achados[0]
    antes = descrever_filtros(cm)
    try:
        if d.acao == "voltar_filtro":
            cm = voltar_filtros(user, cm)
        else:
            cm = ajustar_filtros(user, cm, d.ajuste["filtros"], d.ajuste["remover"])
    except PermissaoNegada as exc:
        return Saida(str(exc), ficha, observacao=Observacao(d.acao, SEM_PERMISSAO, "ajuste não autorizado"))
    except (ValidationError, ValueError) as exc:
        motivo = " ".join(getattr(exc, "messages", [str(exc)]))
        return Saida(motivo, ficha, observacao=Observacao(d.acao, ERRO_METADADOS, motivo))
    verbo = "Voltei os filtros" if d.acao == "voltar_filtro" else "Ajustei os filtros"
    return Saida(f"{verbo} da consulta “{cm.nome}”. Antes: {antes}. Agora: {descrever_filtros(cm)}. "
                 "Para desfazer, peça para voltar o filtro como era antes.", ficha)


def _lembrar(user, d, ficha) -> Saida:
    andamento.avisar("Guardando na memória")
    nota = d.nota.strip()
    if not nota:
        return Saida(d.resposta or "O que você quer que eu guarde?", ficha)
    validade = None
    if d.validade:
        try:
            validade = datetime.strptime(d.validade, "%d/%m/%Y").date()
        except ValueError:
            validade = None
    aviso = ""
    tipo = d.tipo_nota
    if tipo == "regra" and not d.regra:
        from iativa.accounts.authz import permissao_geral

        if permissao_geral(user):
            # M40: TI ensinando sem campo/valor: é uma definição (vai ao modelo), não filtro automático.
            tipo = "termo"
    d = replace(d, tipo_nota=tipo)
    try:
        try:
            n = memoria.lembrar(user, nota, d.alcance, tipo=d.tipo_nota, regra=d.regra, confirmado=d.confirmado,
                                valido_ate=validade)
        except ValidationError as exc:
            if not d.regra or "CPF" in exc.messages[0]:
                raise
            # M40: o mapeamento campo/valor não serve como filtro automático (ex.: campo de outra tabela),
            # mas o ensinamento vale: guarda como definição do termo em vez de perder o pedido.
            n = memoria.lembrar(user, nota, d.alcance, tipo="termo", valido_ate=validade)
            aviso = f" {exc.messages[0]} Por isso guardei como definição, sem virar filtro automático."
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
        texto = d.resposta if d.resposta and "aprova" not in d.resposta.lower() else f"Anotado {alcance}: {nota}"
        return Saida(texto + aviso, ficha)
    extra = " Ela substitui a definição atual do mesmo termo depois de aprovada." if n.substitui_id else ""
    nivel = f"do setor {n.setor.nome}" if n.setor else "da empresa"
    return Saida(f"Registrei como proposta para a memória {nivel}: “{nota}”. Ela só vale depois de aprovada por "
                 f"quem aprova definições nesse nível.{extra}", ficha)


def _investigar(user, c, d, texto, ficha) -> Saida:
    """Cria a tarefa que verifica a lista inteira em segundo plano (worker). Aqui só se valida o plano
    no dicionário, para o modelo poder corrigir na mesma mensagem."""
    from . import tarefas

    andamento.avisar("Conferindo no dicionário os campos para a verificação")
    from .models import SituacaoTarefa, Tarefa

    aguardando = Tarefa.objects.filter(usuario=user, conversa=c, situacao=SituacaoTarefa.AGUARDANDO).order_by(
        "-id").first()
    base = aguardando.solicitacao_base if aguardando is not None else _lista_vista(user, c)
    try:
        plano = investigacao.validar_plano(user, base, d.investigacao or {})
    except investigacao.InvestigacaoInvalida as exc:
        return Saida(str(exc), ficha, observacao=Observacao("investigar_lista", ERRO_PARAMETROS, str(exc)))
    except investigacao.SemPermissao as exc:
        return Saida(str(exc), ficha, observacao=Observacao("investigar_lista", SEM_PERMISSAO, str(exc)))
    except investigacao.Transitoria as exc:
        return Saida(str(exc) + " Tente de novo em instantes.", ficha,
                     observacao=Observacao("investigar_lista", TEMPO_ESGOTADO, str(exc)))
    criterios = [{"nome": x.nome, "valor": x.valor} for x in orq.avaliar_criterios(user, base)]
    objetivo = f"Verificar os vínculos de todos os integrantes da lista “{OBJETIVOS[base.objetivo].nome}”."
    if aguardando is not None:
        # A resposta da pessoa retoma a MESMA tarefa: permissões e plano reconferidos acima; o worker
        # reconsulta a lista e reconstrói as evidências.
        t = tarefas.retomar(aguardando, d.investigacao or {}, plano)
        andamento.avisar("Retomando a verificação com a sua resposta")
    else:
        t = tarefas.criar(user, c, base, d.investigacao or {}, plano, objetivo, criterios)
        andamento.avisar("Verificação de toda a lista agendada em segundo plano")
    registrar("interpretacao", "agente", usuario=user, estado_novo="tarefa_criada", entidade_id=t.pk)
    inicio = d.resposta.strip() or "Vou verificar todas as pessoas da lista, não só o exemplo."
    return Saida(inicio + " O andamento aparece abaixo; pode fechar a página que a verificação continua.",
                 {**ficha, "tipo": "tarefa", "tarefa": t.pk, "opcoes": []},
                 observacao=Observacao("investigar_lista", SUCESSO))


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
