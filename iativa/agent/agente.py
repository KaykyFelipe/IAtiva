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
from iativa.chat.estados import EM_PREPARACAO, TransicaoInvalida
from iativa.chat.models import EstadoSolicitacao as E
from iativa.chat.servicos import cancelar, criar_solicitacao
from iativa.memory.models import TipoValor
from iativa.memory.resolucao import RESOLVIDO, resolver

from . import memoria
from . import orquestrador as orq
from . import andamento, investigacao, prazo
from . import aprendizado
from .aprendizado import candidatos, pode_aprender, pode_aprender_sozinho
from .contratos import BUSCA, OBJETIVOS, PARAMETROS_APRENDIDOS, objetivos_para
from .conversao import exibir
from .decisao import Decisao, DecisaoInvalida, EntradaAgente, validar
from .models import ConsultaModelo, SituacaoModelo
from .provedores import FalhaDecisao, FalhaModelo, obter_provedor

class Interrompido(Exception):
    """A pessoa clicou em "parar" enquanto a IA pensava: nada desta mensagem é aplicado."""


HISTORICO = 12
TENTATIVAS = 2
# M41: pedido explícito de mudar o critério de uma consulta ("ajuste o filtro", "tire os cancelados", "volte como era").
PEDE_AJUSTE_RE = re.compile(r"filtr|ajust|crit[eé]rio|regra|consider|inclu|exclu|\btir[ae]|remov|\bvolt|\bmud|\btroc|desfa",
                            re.I)


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
    # M154: a decisão que de fato executou (ex.: aprender_tabela criou a consulta e rodou preparar_consulta com
    # ela). Receita, memória e controle de repetição usam esta, não a ação externa.
    decisao_efetiva: object = None


def _ciclo() -> dict:
    return {"passos": 3, "retentativas": 1, "tempo_s": 120, **getattr(settings, "IATIVA_AGENTE_CICLO", {})}


def _assinatura(d) -> str:
    # M155: o nome e a descrição da consulta não mudam o método; mesma tabela, colunas e filtros = mesma tentativa.
    consulta = {k: v for k, v in (d.consulta or {}).items() if k not in ("nome", "descricao")} if d.consulta else None
    return json.dumps([d.acao, d.objetivo, sorted(d.parametros), d.alvo, d.nota, consulta, d.regra, d.investigacao,
                       d.ajuste, d.calculo],
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
    # M148: bloqueio/falha anterior não é decisão final; o acesso é reconferido na próxima execução.
    estado = ("Aguardando nova tentativa (o acesso é conferido na execução)" if s.estado in (E.BLOQUEADA, E.FALHOU)
              else s.get_estado_display())
    return {"objetivo": s.objetivo, "nome": obj.nome, "estado": estado,
            "ja_definido": definidos, "faltando": faltando, "completa": not faltando}


CONSULTAS_CONTEXTO = 6
_DINHEIRO_RE = re.compile(r"R\$\s*\d")


def _consultas_conversa(user, c, defs) -> list[dict]:
    """M48: o que já foi consultado NESTA conversa (consulta, tabela, critérios, filtros e colunas), para a IA
    explicar uma consulta pedida antes ("quais filtros você usou?", "de onde veio esse total?"). Só metadados:
    nunca linhas, totais ou quantidades do ERP. Resultado fora das permissões atuais da pessoa fica de fora."""
    from iativa.chat.resultados import restaurar
    from .aprendizado import descrever_filtros

    out = []
    msgs = (c.mensagens.filter(papel="ia", solicitacao__isnull=False).select_related("solicitacao")
            .order_by("-id")[:CONSULTAS_CONTEXTO])
    for m in msgs:
        s = m.solicitacao
        if not s.objetivo or s.objetivo not in OBJETIVOS:
            continue
        try:
            item = _estado(user, s, defs) or {}
        except Exception:
            continue
        obj = OBJETIVOS[s.objetivo]
        item = {"mensagem": m.pk, "consulta": obj.nome, "codigo": s.objetivo, "descricao": obj.descricao[:300],
                "tabelas": list(obj.tabelas), "criterios": item.get("ja_definido", {}),
                "situacao": (m.ficha or {}).get("tipo", "")}
        cm = ConsultaModelo.objects.filter(codigo=s.objetivo).first() if obj.modelo_id else None
        if cm is not None:
            item["filtros"] = descrever_filtros(cm, user)
        res = restaurar(user, m.resultado) if m.resultado else None
        if res is not None and not getattr(res, "consolidado", False):
            item["colunas"] = list(res.colunas)
            item["premissas"] = [p for p in res.premissas if not _DINHEIRO_RE.search(p)][:12]
        out.append(item)
    return out[::-1]


_FALHA_ANTERIOR_RE = re.compile(
    r"^(Não terminei esta resposta|Sem permissão de consulta|A fonte não é autorizada|Esta receita não está liberada|"
    r"Essa fonte pertence a outro setor|O ERP ou a IA não responderam|Não consegui confirmar o assunto|"
    r"A consulta depende de acesso vigente|Permissão insuficiente)")
_FICHAS_FALHA = {"bloqueada", "falha_execucao", "falha_modelo"}
FALHA_NO_HISTORICO = ("[o sistema não concluiu esta resposta (falha ou bloqueio passageiro, já revisto). Não é motivo "
                      "para recusar nem para dizer que falta permissão: o sistema confere o acesso a cada execução. "
                      "Execute a consulta de novo.]")


def _falha_anterior(m) -> bool:
    """M148: recusa/falha do próprio sistema numa mensagem anterior (não é resposta com dados)."""
    if m.papel == "usuario" or m.resultado:
        return False
    ficha = m.ficha if isinstance(m.ficha, dict) else {}
    if ficha.get("dado_protegido"):
        return False  # recusa de dado pessoal protegido continua valendo como contexto
    return ficha.get("tipo") in _FICHAS_FALHA or bool(_FALHA_ANTERIOR_RE.match((m.texto or "").strip()))


def _historico(c, ate=None) -> list[dict]:
    msgs = c.mensagens.all()
    if ate is not None:
        msgs = msgs.filter(pk__lt=ate)
    # M148: recusas e falhas antigas do sistema no histórico levavam o modelo a responder "sem permissão" sem
    # executar, mesmo depois de corrigido o acesso. Ficam como aviso neutro para ele tentar de novo.
    return [{"quem": "usuario" if m.papel == "usuario" else "iativa",
             "texto": FALHA_NO_HISTORICO if _falha_anterior(m) else m.texto[:600]}
            for m in reversed(list(msgs.order_by("-criado_em", "-id")[:HISTORICO]))]


def _notas(user, texto: str) -> list[str]:
    """Notas relevantes ao pedido (não as 40 mais recentes), com o tipo de cada uma."""
    from . import objetivos as coord
    notas = [memoria.rotulo_nota(n) for n in memoria.notas_relevantes(user, texto)]
    if coord.pede_intervalo(coord._norm(texto)):
        notas.append(INTERVALO_REGRA)  # M108: orienta a 1ª consulta, não só depois de errar a base
    if coord.ano_admissoes_e_desligamentos(texto):
        notas.append(
            "[regra do sistema] Conte vínculos com as duas datas no ano civil pedido na mesma consulta. "
            "Use filtro-campo com intervalos para admissão e desligamento, o ano completo em periodo-analise "
            "se exigido, agregacao contagem:* por filial e filial-analise TODAS. A entrega é só essa interseção "
            "(filial e quantidade); totais separados de cada evento e listas nominais não foram pedidos.")
    from .comparacao import contagens_mensais
    if contagens_mensais(texto):
        notas.append(
            "[regra do sistema] Este pedido tem duas contagens independentes. Use o Protheus local: "
            "FP0_ZDTASS é a assinatura do contrato de locação e FPA_DTINI é o início registrado da locação. "
            "Procure ou aprenda uma consulta autorizada para cada evento e conte por mês e filial no intervalo "
            "completo. Use o identificador da entidade e confira a granularidade antes de contar. Não reaproveite "
            "a data de assinatura, a data de cadastro ou a previsão de entrega como início da locação.")
    return notas


INTERVALO_REGRA = ("[regra do sistema] Pedido de prazo entre dois eventos (ex.: admitidos em 2026 que saíram antes de "
                   "90 dias): a consulta base filtra o período pela data do PRIMEIRO evento citado (admissão), nunca "
                   "pela do segundo (demissão), e traz a data do segundo evento como coluna; depois calcular_resultado "
                   "com operacao intervalo_entre_datas, campo = data do primeiro evento, campo_final = data do segundo, "
                   "limite = dias pedidos.")


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


# M154: com as tabelas da descoberta em mãos, a decisão é uma destas três. Pedir de novo a consulta antiga
# (preparar_consulta) ou "responder" sem consultar gastava o prazo com decisões equivalentes.
ETAPA_MONTAR = ("criar_consulta", "aprender_tabela", "conversar")
ETAPA_MONTAR_TEXTO = (
    "Nesta etapa só valem três decisões: (1) criar_consulta com uma tabela de tabelas_para_aprender, escolhendo a "
    "entidade contada (a chave da tabela), os campos de classificação pelos domínios do dicionário e os filtros que o "
    "pedido exige; (2) aprender_tabela para outra fonte, dizendo em resposta por que as tabelas atuais não servem e "
    "em alvo o assunto ou os códigos das tabelas; (3) conversar com UMA pergunta objetiva, terminada em ?, sobre o "
    "significado de negócio de um termo do pedido que muda o resultado. Nunca peça tabela, campo ou SQL à pessoa.")
_CORRECOES_CAMPO = {
    "acao": "Use uma das ações do esquema{etapa}.",
    "objetivo": "Use um código de objetivo da lista objetivos_disponiveis ou deixe vazio.",
    "parametro": "Use só códigos de parâmetro da lista; o valor vai em 'valor'.",
    "consulta": "Na criar_consulta preencha consulta com tabela (3 letras), nome e colunas com nomes técnicos.",
    "calculo": "Na calcular_resultado preencha calculo.operacao com uma operação da lista e mensagem com um inteiro.",
    "ajuste": "Na ajustar_filtro preencha ajuste.filtros (campo, operador 'igual' ou 'diferente', valor) ou remover.",
}


def _fora_da_etapa(d, entrada) -> str:
    permitidas = tuple(getattr(entrada, "acoes_permitidas", ()) or ())
    if not permitidas:
        return ""
    if d.acao not in permitidas:
        return (f"A ação '{d.acao}' não vale nesta etapa. "
                + (ETAPA_MONTAR_TEXTO if permitidas == ETAPA_MONTAR else "Use: " + ", ".join(permitidas) + "."))
    if permitidas == ETAPA_MONTAR and d.acao == "conversar" and not d.resposta.strip().endswith("?"):
        return "Resposta sem consulta não conclui o pedido. " + ETAPA_MONTAR_TEXTO
    return ""


def _correcao_do_campo(campo, entrada) -> str:
    """M154: a próxima tentativa recebe a restrição específica do campo recusado, não um aviso genérico."""
    raiz = str(campo).split(".")[0].split(":")[0].strip()
    permitidas = tuple(getattr(entrada, "acoes_permitidas", ()) or ())
    etapa = (": " + ", ".join(permitidas)) if permitidas else ""
    especifica = _CORRECOES_CAMPO.get(raiz, "").format(etapa=etapa)
    return (f"Sua resposta anterior foi recusada no campo '{campo}'. " + (especifica + " " if especifica else "")
            + "Responda de novo no formato do esquema: só os campos previstos, códigos de objetivo e de parâmetro da "
              "lista, filtros com operador 'igual' ou 'diferente', tabela com 3 letras e campos com o nome técnico.")


def _interpretar(provedor, entrada, codigos_obj, codigos_par):
    """M138: resposta do modelo fora do formato volta para ele com o motivo (antes repetia a mesma entrada às cegas
    e, depois de 3 recusas iguais, o pedido parava como "a IA não respondeu a tempo")."""
    import logging
    for _ in range(TENTATIVAS):
        bruta = None
        try:
            from . import tempos
            with tempos.medir("decisao"):
                bruta = provedor.decidir(entrada)
            d = validar(bruta, codigos_obj, codigos_par)
            fora = _fora_da_etapa(d, entrada)
            if not fora:
                return d
            logging.getLogger(__name__).warning("decisao_fora_da_etapa: acao=%s", d.acao[:40])
            entrada = replace(entrada, observacoes=list(getattr(entrada, "observacoes", None) or []) + [{
                "acao": d.acao, "situacao": "etapa_errada", "motivo": fora}])
            continue
        except (DecisaoInvalida, FalhaDecisao) as exc:
            motivo = exc.campo if isinstance(exc, FalhaDecisao) else str(exc)[:120]
            acao = str(bruta.get("acao", ""))[:40] if isinstance(bruta, dict) else ""
            logging.getLogger(__name__).warning("decisao_invalida: campo=%s acao=%s", motivo, acao)
            entrada = replace(entrada, observacoes=list(getattr(entrada, "observacoes", None) or []) + [{
                "acao": "formato_da_resposta", "situacao": "decisao_invalida",
                "motivo": _correcao_do_campo(motivo, entrada)}])
            continue
        except FalhaModelo as exc:
            if exc.temporaria:
                raise
    raise FalhaModelo("sem decisão válida")


def _aliases_da_resposta(texto):
    termos = re.split(r"\s*(?:,|/|\+|;|\be\b)\s*|\s+", texto.strip().rstrip("."), flags=re.I)
    if not 1 <= len(termos) <= 4 or not all(re.fullmatch(r"[A-Za-z][A-Za-z0-9]{2}", t) for t in termos):
        return []
    aliases = list(dict.fromkeys(t.upper() for t in termos))
    if len(aliases) == 1 and aliases[0] in {"SIM", "NAO", "OK"}:
        return []
    if len(aliases) == 1 and not (termos[0].isupper() or any(c.isdigit() for c in termos[0])):
        return []
    return aliases


def _pedido_com_fontes(user, conversa, texto, mensagem_id):
    """Uma indicação curta de tabelas complementa o pedido anterior da mesma pessoa."""
    aliases = _aliases_da_resposta(texto)
    if not aliases:
        return texto, []
    anteriores = conversa.mensagens.filter(papel="usuario")
    if mensagem_id is not None:
        anteriores = anteriores.filter(pk__lt=mensagem_id)
    for anterior in anteriores.order_by("-pk")[:5]:
        if anterior.autor_id != user.pk:
            return texto, []
        if not _aliases_da_resposta(anterior.texto):
            break
    else:
        return texto, []
    from iativa.metadata.catalogo import abrir_catalogo
    try:
        with abrir_catalogo() as cat:
            existentes = cat.dicionario.tabelas(aliases)
    except Exception:
        return texto, []
    if not existentes:
        return texto, []
    return anterior.texto + "\nFontes indicadas para este pedido: " + ", ".join(aliases), aliases


def conversar(user, c, texto: str, provedor=None, mensagem_usuario_id=None, interrompido=None, acomp=None,
              observacoes=None, contexto_id=None) -> Saida:
    from .escopo import pedido_codigo, RECUSA
    if pedido_codigo(texto):
        return Saida(RECUSA, ficha={"tipo": "fora_escopo"})
    from . import estrutura
    if estrutura.solicitado(texto):
        return estrutura.consultar(user)
    objetivos = objetivos_para(user)
    administrador = pode_aprender(user)
    if not objetivos and not administrador and not setores_autorizados(user, Permissao.CONSULTAR):
        registrar("acesso_negado", "agente", usuario=user, estado_novo="dominio", resultado="negado")
        return Saida("Seu usuário ainda não tem acesso a consultas. Fale com o administrador.")

    from . import planejamento
    from .contagem import contextualizar, contextualizar_separadas
    contexto_id = contexto_id or mensagem_usuario_id
    if acomp is None:
        texto = pedido_esclarecido(c, texto, contexto_id)
    texto, fontes = _pedido_com_fontes(user, c, texto, contexto_id)
    if not fontes:
        from .comparacao import contextualizar_ano
        com_ano = contextualizar_ano(c, texto, contexto_id)
        if acomp is not None and com_ano != texto:
            acomp.texto = com_ano
        texto = contextualizar(user, c, contextualizar_separadas(c, user, com_ano, contexto_id))
        if acomp is not None:
            acomp.texto = texto
    try:
        provedor = provedor or obter_provedor(usuario=user)
        analise = planejamento.preparar(user, c, texto, provedor, _historico(c, contexto_id))
    except planejamento.DicionarioIndisponivel:
        return Saida("O dicionário do Protheus está indisponível. Tentei conectar duas vezes para localizar "
                     "a fonte do pedido. A consulta pode ser refeita quando a conexão voltar.",
                     ficha={"tipo": "falha_execucao"})
    except PermissaoNegada as exc:
        return Saida(str(exc), ficha={'tipo': 'bloqueada'})
    except ValidationError as exc:
        return Saida(exc.messages[0], ficha={'tipo': 'esclarecimento_setor'})
    except FalhaModelo as exc:
        import logging
        logging.getLogger(__name__).warning('planejamento_falhou: %s', exc)
        if acomp is not None:  # M127: na continuação, falha do modelo na triagem é passageira: tenta de novo depois
            acomp.anotar('classificacao', 'modelo_falhou')
            return _encerrar_sem_cumprir(acomp, user, c, mensagem_usuario_id, None, None, 'modelo_falhou', [])
        if exc.temporaria:
            nome = getattr(provedor, "nome", "")
            servico = {"claude-code": "Claude", "anthropic": "Claude", "chatgpt": "ChatGPT"}.get(nome, "modelo de IA")
            return Saida(f"O serviço {servico} está indisponível no momento. A consulta ainda não começou.",
                         ficha={"tipo": "falha_modelo", "provedor": nome})
        return Saida("O modelo não devolveu um plano válido para a consulta. Tente novamente.",
                     ficha={"tipo": "falha_modelo"})
    except ImproperlyConfigured:
        return Saida('Não consegui confirmar o assunto e o acesso deste pedido agora. Tente novamente.',
                     ficha={'tipo': 'falha_modelo'})
    if fontes:
        analise["fontes_indicadas"] = fontes
    if analise['natureza'] == 'fora':
        return Saida(RECUSA, ficha={'tipo': 'fora_escopo'})
    if mensagem_usuario_id is not None and not c.titulo:
        from iativa.chat.conversas import definir_titulo
        definir_titulo(c, analise.get('titulo'), texto)

    from iativa.accounts.authz import limitar_consulta
    from iativa.accounts.models import Department
    ids = Department.objects.filter(codigo__in=analise['setores']).values_list('pk', flat=True)
    with limitar_consulta(user, ids):
        token = planejamento.provedor_do_pedido.set(provedor)
        try:
            return _conversar_autorizado(user, c, texto, provedor, mensagem_usuario_id, interrompido, acomp, observacoes,
                                        analise, contexto_id)
        finally:
            planejamento.provedor_do_pedido.reset(token)


def _conversar_autorizado(user, c, texto, provedor, mensagem_usuario_id, interrompido, acomp, observacoes, analise,
                          contexto_id):
    from . import planejamento
    objetivos = objetivos_para(user)
    administrador = pode_aprender(user)
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
        candidata = _avaliar_e_executar(user, atual, "", erros, {"provedor": "direto", "opcoes": []})
        from . import revisao
        obj = OBJETIVOS[atual.objetivo] if atual.objetivo in OBJETIVOS else None
        pedido = f"Executar {obj.nome if obj else 'a consulta em andamento'} com o critério informado: {texto}"
        falta_rev = revisao.revisar(user, provedor, pedido, [], candidata)
        if not falta_rev:
            return candidata
        observacoes = [*(observacoes or []), Observacao("revisao", "entrega_nao_atende", falta_rev)]
    try:
        trechos = [{"documento": t.titulo_documento, "secao": t.secao, "texto": t.texto}
                   for t in orq.descoberta.conhecimento(user, orq.DOMINIO, texto, limite=3)]
    except Exception:
        trechos = []
    aprendidos = _aprendidos(administrador)
    andamento.avisar("Lendo o pedido e o histórico da conversa")
    from . import protegidos
    dado = protegidos.protegido(texto) if acomp is None else ""
    if dado and not protegidos.liberado(user, dado):
        # M85: dado pessoal protegido (ex.: aniversários = data de nascimento): responde na hora com o motivo.
        andamento.avisar("O pedido envolve dado pessoal protegido")
        return Saida(protegidos.resposta(dado), ficha={"tipo": "bloqueada", "dado_protegido": dado})
    from . import definicoes
    falta = definicoes.pergunta(user, c, texto) if acomp is None else None
    if falta is not None:
        # M74: definição de negócio sem aprovação vira pergunta objetiva; não supõe critério nem consulta.
        andamento.avisar("Falta uma definição de negócio; perguntando")
        return Saida(falta[1], ficha={"tipo": "definicao", "definicao_pedida": falta[0]})
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
    from . import calculos
    try:
        consultas_conversa = _consultas_conversa(user, c, defs)
    except Exception:
        consultas_conversa = []  # contexto auxiliar: nunca impede a resposta
    planejamento_pedido = {"modo": "executar", "analise": analise}
    if analise.get("natureza") == "dados":
        from iativa.knowledge.memoria_tecnica import tabelas_candidatas
        referencias = {alias for alias, _ in tabelas_candidatas(texto)}
        planejamento_pedido["receitas_relacionadas"] = [
            o.codigo for o in objetivos if referencias.intersection(o.tabelas)][:8]
    entrada = EntradaAgente(
        planejamento=planejamento_pedido,
        consultas_conversa=consultas_conversa,
        resultado_anterior=calculos.contexto(user, c),
        lista_anterior=lista,
        mensagem=texto, hoje=timezone.localdate().isoformat(), historico=_historico(c, contexto_id),
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
        saida = _ciclo_agente(user, c, texto, provedor, interrompido, entrada, objetivos, codigos_par, defs, atual,
                             administrador, cfg, cobertura, pede_cobertura, mensagem_id=mensagem_usuario_id,
                             acomp=acomp, observacoes=observacoes)
    planejamento.guardar(user, c, mensagem_usuario_id, texto, analise, saida)
    return saida


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

    return {"codigo": cm.codigo, "tabela": cm.tabela, "filtros": descrever_filtros(cm, user)}


SEM_TEMPO = "O tempo total para responder esta mensagem acabou antes de eu concluir."


def _ciclo_agente(user, c, texto, provedor, interrompido, entrada, objetivos, codigos_par, defs, atual,
                  administrador, cfg, cobertura=None, verificar=False, mensagem_id=None, acomp=None,
                  observacoes=None) -> Saida:
    """M48: roda o ciclo e marca na ficha o formato que a pessoa pediu ("direto": só o valor)."""
    from . import objetivos as coord

    ac = acomp or coord.Acompanhamento(texto)
    if not ac.contrato:
        ac.contrato = entrada.planejamento.get('analise', {}).get('entregas', [])
    from . import fidelidade
    anterior = ac.contrato
    # M125: também na continuação e na retomada após correção (contrato gravado por uma versão anterior).
    ac.contrato = fidelidade.ajustar(texto, anterior, user)
    ac.cumpridas = fidelidade.remapear_cumpridas(anterior, ac.contrato, ac.cumpridas)
    from . import contagem as admissoes
    if admissoes.assunto_acompanhado(user, texto) and not any(e.get('tabela') for e in ac.contrato):
        anterior = ac.contrato
        ac.contrato = admissoes.planejar(texto, {'entregas': anterior}, user)['entregas']
        ac.cumpridas = [i for i in ac.cumpridas if i < len(anterior) and anterior[i] == ac.contrato[i]]
    from . import entregas, comparacao
    anterior = ac.contrato
    from . import tabela
    if not any(e.get('tabela') for e in anterior):  # M130: a tabela já planejada (especificação do modelo) fica
        ac.contrato = tabela.planejar(texto, comparacao.planejar(texto, entregas.planejar(texto, {'entregas': anterior})))['entregas']
    ac.cumpridas = [i for i in ac.cumpridas if i < len(anterior) and i < len(ac.contrato) and anterior[i] == ac.contrato[i]]
    if ac.contrato:
        ac.partes_faltam = 0
    saida = _ciclo_passos(user, c, texto, provedor, interrompido, entrada, objetivos, codigos_par, defs, atual,
                          administrador, cfg, cobertura, verificar, mensagem_id, ac, observacoes)
    if ac.formato and saida is not None:
        saida.ficha = {**(saida.ficha or {}), "formato": ac.formato}
    # Tarefa em segundo plano: o "vou verificar…" é o aviso do trabalho que continua, não narração.
    if saida is not None and saida.resultado is not None and (saida.ficha or {}).get("tipo") != "tarefa":
        saida.texto = _sem_narracao(saida.texto)
    return saida


def _respeitar_preferencias(user, d, texto: str):
    """M55: valor de parâmetro que o modelo preencheu por conta própria (a pessoa não disse) não passa por cima
    da preferência salva dela: tira o valor e deixa a resolução da memória aplicar a preferência."""
    if d.acao != "preparar_consulta" or not d.parametros:
        return d
    from django.db.models import Q
    from django.utils import timezone

    from iativa.memory.models import Escopo, ParameterBinding, Situacao

    codigos = {c for c, _ in d.parametros}
    com_preferencia = set(ParameterBinding.objects.filter(
        usuario=user, escopo=Escopo.USUARIO, situacao=Situacao.APROVADA, definicao__codigo__in=codigos).filter(
        Q(valido_ate__isnull=True) | Q(valido_ate__gt=timezone.now())).values_list("definicao__codigo", flat=True))
    if not com_preferencia:
        return d
    t = (texto or "").lower()
    manter = tuple((c, v) for c, v in d.parametros
                   if c not in com_preferencia or (str(v).strip() and str(v).strip().lower() in t))
    return replace(d, parametros=manter) if manter != d.parametros else d


_PLURAL_DA_LISTA = re.compile(r"\b(deles|delas|desses|dessas|destes|destas|de cada um|dos admitidos|dos demitidos|"
                              r"da lista|dessa lista|desta lista)\b")


def _atributo_da_lista(user, c, d, texto: str):
    """M62: "qual a função deles?" depois de uma lista de funcionários é um dado da mesma lista, não o histórico
    de cargo de uma pessoa: refaz a consulta anterior (que já traz a função) com os mesmos critérios."""
    if d.acao != "historico_cargo" or not _PLURAL_DA_LISTA.search((texto or "").lower()):
        return d
    ultima = _ultima(user, c)
    if ultima is None or ultima.objetivo not in OBJETIVOS or not OBJETIVOS[ultima.objetivo].modelo_id:
        return d
    if OBJETIVOS[ultima.objetivo].tabelas[0] != "SRA":
        return d
    return replace(d, acao="preparar_consulta", objetivo=ultima.objetivo, parametros=())


_FIXAS_COM_TOTAL = ("titulos-em-atraso", "titulos-a-vencer")
_PEDE_POR_FILIAL_RE = re.compile(
    r"\b(?:por|em cada|de cada|separad\w*\s+por)\s+filia(?:l|is)\b|\bfilial\s+a\s+filial\b"
)


def _calculo_implicito(d, texto: str):
    """M54/M143: nas consultas fixas e aprendidas, garante 'por filial' quando pedido explicitamente na pergunta."""
    t = " ".join((texto or "").lower().split())
    if d.acao == "preparar_consulta" and d.objetivo in _FIXAS_COM_TOTAL:
        if any(c == "agregacao" for c, _ in d.parametros):
            return d
        if _PEDE_POR_FILIAL_RE.search(t):
            calc = "por filial"
        elif (re.match(r"^(e\s+)?quanto\b", t) and not re.search(r"\b(quais|quem|lista|liste|mostre|detalh|cliente)", t)):
            calc = "total"
        else:
            return d
        return replace(d, parametros=d.parametros + (("agregacao", calc),))
    if d.acao in ("preparar_consulta", "criar_consulta") and _PEDE_POR_FILIAL_RE.search(t):
        # M143: quando a pessoa pede "por filial" junto de outra dimensão ("quanto cada vendedor vendeu por filial")
        # e o modelo manda só "soma:D2_TOTAL por F2_VEND1", sem "por filial" o serviço somaria as filiais.
        novos, mudou = [], False
        for k, v in d.parametros:
            if k == "agregacao" and " por " in str(v).lower() and "filial" not in str(v).lower() and not re.search(r"\btop\s*\d", str(v), re.I):
                v = f"{str(v).strip()} por filial"
                mudou = True
            novos.append((k, v))
        if mudou:
            if not any(k == "filial-analise" for k, _ in novos):
                novos.append(("filial-analise", "TODAS"))
            return replace(d, parametros=tuple(novos))
    return d


def _sem_narracao(texto: str) -> str:
    """M53: "Vou levantar…", "Vou consultar…" já apareceram ao vivo no andamento; na resposta final, com o
    resultado na tela, ficam só os fatos (o que foi executado e os números conferidos)."""
    from . import objetivos as coord

    partes = [p for p in (texto or "").split("\n\n") if p.strip()]
    limpas = [p for p in partes if not coord.anuncia(p)]
    return "\n\n".join(limpas) if limpas else (texto or "")


def _ciclo_passos(user, c, texto, provedor, interrompido, entrada, objetivos, codigos_par, defs, atual,
                  administrador, cfg, cobertura=None, verificar=False, mensagem_id=None, acomp=None,
                  observacoes=None) -> Saida:
    """Ciclo com evidência de cumprimento (M38). Cada passo: decidir → agir → medir o que foi entregue
    contra o que o pedido exige (`objetivos.py`). Consulta que deu certo não encerra uma verificação;
    texto que só anuncia ou repete não é progresso; sem progresso, para e encaminha em vez de insistir."""
    from . import diagnostico, historico_funcional
    from . import objetivos as coord

    ac = acomp or coord.Acompanhamento(texto)
    feitas, observacoes, saida = set(), list(observacoes or []), None
    parametros_recusados = set()
    planos_reprovados = set()
    previa_comprovada = None  # M155: blocos aprovados pela revisão antes de uma reprovação parcial
    reprovacoes_objetivo = {}  # M154: quantas vezes a revisão reprovou cada consulta nesta mensagem
    previa = None  # etapa intermediária concluída (ex.: a lista) que a próxima etapa completa
    previa_parte = None  # M41: resposta já conferida da 1ª pergunta de uma mensagem com várias
    resultado_base = None  # M45: última consulta desta mensagem; só em memória, autorizada pelo backend
    causa = "passos"
    # M121: pedido com várias entregas ganha passos proporcionais (cerca de 2 por entrega, até 14), em vez de
    # empurrar quase tudo para o segundo plano.
    passos = min(14, max(int(cfg["passos"]), 2 * len(ac.contrato) + 1))
    # M125: receita já verificada para este tipo de pedido (e acessível à pessoa) é o 1º passo, sem o modelo.
    from . import receita_pedido as aprendizado_receita
    receita_d, receita_id = (None, None)
    fila_receita, passos_verificados = [], []
    texto_receita = aprendizado_receita.com_contexto(texto, entrada.historico)
    retomada_transitoria = (ac.registro is not None
                            and ac.registro.motivo in {f"continua: {situacao}" for situacao in TRANSITORIAS})
    if (not observacoes or retomada_transitoria) and not ac.trilha:
        try:
            if len(ac.contrato) > 1:
                fila_receita, receita_id = aprendizado_receita.composta_para(texto_receita, ac.contrato, objetivos)
                if not fila_receita and not entrada.planejamento.get("analise", {}).get("fontes_indicadas"):
                    fila_receita = aprendizado_receita.compor_partes(ac.contrato, objetivos)
            else:
                receita_d, receita_id = aprendizado_receita.receita_para(texto, objetivos)
        except Exception:
            receita_d, receita_id = None, None
    from .contagem import consultas_separadas
    if not fila_receita and not ac.trilha:
        deterministicas = consultas_separadas(texto, objetivos)
        if deterministicas:
            fila_receita, receita_d = deterministicas, None
    from . import tabela
    spec_tab = tabela.com_tabela(ac)
    if spec_tab and not feitas:
        # M129: tabela de vários eventos/períodos com consultas que a IA já sabe fazer: monta direto, sem modelo
        # nem manutenção. Sem consulta para algum evento (None), o ciclo normal decide.
        try:
            s_tab = tabela.executar(user, c, texto, defs, objetivos, interrompido, spec=spec_tab)
        except ValidationError:
            s_tab = None
        if s_tab is not None:
            ac.anotar("tabela", "sucesso", coord.CALCULO)
            ac.cumpridas = [i for i, e in enumerate(ac.contrato) if e.get("tabela")]
            if not ac.pendentes():
                return _fechar(ac, user, c, mensagem_id, s_tab, coord.CALCULO)
        elif isinstance(spec_tab, dict) and spec_tab.get('antes'):
            # M130: sem consulta para algum evento (ou célula falhou): volta às entregas originais e o ciclo normal
            # responde; a tabela não cumprida não manda à manutenção o que a IA já sabe consultar.
            ac.contrato, ac.cumpridas = list(spec_tab['antes']), []
    for _ in range(max(1, passos)):
        if prazo.esgotado():
            causa = "prazo"
            break
        entrada_passo = entrada if not observacoes else replace(
            entrada, solicitacao_atual=_estado(user, atual, defs), observacoes=[o.para_modelo() for o in observacoes])
        fontes = entrada.planejamento.get("analise", {}).get("fontes_indicadas", [])
        andamento.avisar("Decidindo o que fazer" if not observacoes else
                         "Ajustando o caminho: " + observacoes[-1].detalhe[:110])
        passo_programado = False
        try:
            if fontes and not feitas and entrada.planejamento["analise"]["natureza"] == "dados":
                d = Decisao("Vou conferir as fontes indicadas para concluir o pedido.", "aprender_tabela", "",
                            (), " ".join(fontes), "", False, ())
            elif fila_receita:
                passo_programado = True
                d = fila_receita.pop(0)
                andamento.avisar("Executando um passo da receita já verificada")
            elif receita_d is not None and not feitas:
                passo_programado = True
                d = receita_d
                andamento.avisar("Usando a receita já verificada para este tipo de pedido")
            else:
                d = _interpretar(provedor, entrada_passo, [o.codigo for o in objetivos], codigos_par)
        except prazo.PrazoEsgotado:
            causa = "prazo"
            break
        except FalhaModelo as exc:
            # Motivo é código do provedor (tipo de erro, status), nunca a pergunta nem dados.
            import logging
            logging.getLogger(__name__).warning("modelo falhou (%s): %s", provedor.nome, exc)
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
        from iativa.chat import erros as erros_ia
        erros_ia.anotar_passo(d, observacoes)  # M149: se a resposta terminar em erro, o registro mostra cada passo
        if interrompido is not None and interrompido():
            raise Interrompido  # antes de qualquer ação (e de consultar o ERP)
        if prazo.esgotado():
            causa = "prazo"
            break  # o modelo respondeu tarde: a ação não é executada com o prazo vencido
        if entrada.planejamento.get('analise', {}).get('natureza') == 'geral' and d.acao not in (
                'conversar', 'fora_escopo', 'lembrar', 'listar_memorias', 'esquecer_memoria',
                'listar_pendencias', 'aprovar_pendencia', 'rejeitar_pendencia', 'cancelar_consulta'):
            return Saida('Para consultar dados, preciso confirmar o assunto e o setor da consulta.',
                         ficha={'tipo': 'esclarecimento_setor'})
        from .escopo import resposta_codigo, RECUSA
        if resposta_codigo(d.resposta):
            return Saida(RECUSA, ficha={"tipo": "fora_escopo"})
        if d.acao == "fora_escopo":
            # Não usar a resposta livre do modelo: não responder ao assunto nem
            # ensinar à manutenção capacidades pessoais fora da finalidade do produto.
            return Saida("Sou a IAtiva, assistente da Ativa Locação. Posso ajudar com dados, processos e "
                         "atividades da empresa dentro das suas permissões. Relacione seu pedido ao trabalho "
                         "para eu ajudar.", ficha={"tipo": "fora_escopo"})
        if diagnostico.solicitado(texto) and diagnostico.reutilizavel(texto) and d.acao == 'conversar':
            anterior = _ultima(user, c)
            if (anterior is not None and anterior.objetivo in {o.codigo for o in objetivos}
                    and OBJETIVOS[anterior.objetivo].modelo_id):
                from iativa.query.servico import repetir
                atual = repetir(user, anterior, conversa=c)
                d = replace(d, acao='executar_consulta', objetivo=atual.objetivo, resposta='')
        if (historico_funcional.solicitado(texto) or historico_funcional.contexto_estagio(user, c, texto)) and d.acao in (
                "conversar", "preparar_consulta", "executar_consulta", "aprender_tabela", "criar_consulta", "historico_cargo"):
            # Histórico funcional exige encadear fontes: listar matrículas não conclui esse pedido.
            nome = historico_funcional.nome_contexto(user, c, texto, d)
            d = replace(d, acao="historico_cargo", alvo=nome)
        if d.acao == "conversar" and resultado_base is None:
            # Dias úteis/corridos entre duas datas digitadas: é cálculo de calendário, não conversa.
            from . import calculos
            metrica = coord.metrica_exigida(texto)
            if metrica in ("dias_uteis", "dias_corridos") and calculos.intervalo_do_texto(texto):
                d = replace(d, acao="calcular_resultado", calculo={"operacao": metrica, "mensagem": 0,
                                                                   "campo": "", "campo_final": ""})
        if (entrada.planejamento.get("fontes_inconclusivas") and d.acao in ("conversar", "falta_capacidade")
                and not any(o.acao == "aprender_tabela" for o in observacoes)
                and pode_aprender_sozinho(user)):
            d = replace(d, acao="aprender_tabela", alvo=texto, consulta=None, opcoes=(), resposta="")
        d = _calculo_implicito(d, texto)
        d = _respeitar_preferencias(user, d, texto)
        d = _atributo_da_lista(user, c, d, texto)
        from . import entregas
        d = entregas.orientar(user, texto, d, objetivos)
        from . import comparacao
        if (comparacao.periodos(texto) and d.acao in ('investigar_divergencia','executar_consulta','preparar_consulta')
                and comparacao.deste_passo(ac, d)):
            d = replace(d,acao='preparar_consulta',entrega='calculo')
            if not ac.trilha: ac.exig = coord.CALCULO
        if entregas.somente_lista(texto) and d.acao == 'preparar_consulta' and not ac.trilha:
            ac.exig = coord.CONSULTA
        if d.acao == "preparar_consulta" and ac.exig == coord.HISTORICO and not ac.trilha:
            ac.exig = ""  # M62: virou dado da lista anterior; a exigência vem da ação corrigida
        ac.definir(d)
        from . import contagem as admissoes
        d = admissoes.orientar(user, texto, d, objetivos)
        d = comparacao.agrupar_evento_mensal(ac, d)
        if d.acao not in ("conversar", "falta_capacidade") and d.resposta.strip():
            andamento.avisar(d.resposta.strip().split("\n")[0])  # M53: o que a IA vai fazer aparece na hora
        if (d.acao == "falta_capacidade" and pode_aprender_sozinho(user)
                and entrada.planejamento["analise"]["natureza"] == "dados"
                and not any(p.startswith(("aprender_tabela:", "criar_consulta:")) for p in ac.trilha)):
            d = replace(d, acao="aprender_tabela", alvo=texto, consulta=None)
        if d.acao == "falta_capacidade":
            # M53: o modelo verificou que nenhum caminho existente resolve: a manutenção implementa já.
            ac.capacidade = d.capacidade
            ac.anotar("falta_capacidade", "declarada")
            from iativa.manutencao.incidentes import automatica
            andamento.avisar("Não tenho isso ainda; acionando a manutenção para implementar" if automatica()
                             else "Não tenho isso ainda; registrando para a TI")
            causa = "capacidade_declarada"
            break
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
        if passo_programado and d.atende and len(ac.contrato) > 1:
            assinatura += repr(d.atende)
        if assinatura in parametros_recusados and cobertura is None:  # M117: vale para qualquer exigência
            alternativa = admissoes.recuperar_filtro_do_ano(texto, d, objetivos)
            if alternativa is not None and (candidata := _assinatura(alternativa)) not in feitas:
                d, assinatura = alternativa, candidata
        if assinatura in feitas and cobertura is None and ac.contrato:
            if (len(ac.contrato) == 1 and ac.exig == coord.CALCULO and ac.pendentes()
                    and previa is not None and not ac.cumpridas):
                alternativa = admissoes.recuperar_filtro_do_ano(texto, d, objetivos, entrega_pendente=True)
                if alternativa is not None and (candidata := _assinatura(alternativa)) not in feitas:
                    d, assinatura, previa = alternativa, candidata, None
        if assinatura in feitas and cobertura is None and ac.contrato:
            for alternativa in comparacao.alternativas_mensais(ac, d, objetivos):
                candidata = _assinatura(alternativa)
                if candidata not in feitas:
                    d, assinatura = alternativa, candidata
                    break
        # Se a decisão executaria de novo a consulta rejeitada, use o dicionário
        # e o mesmo fluxo autorizado de criação de consulta, dentro do orçamento do pedido.
        # M154: um método (consulta) reprovado duas vezes não volta só com outro parâmetro: a próxima tentativa
        # muda a fonte (descoberta) ou pergunta a definição; não gasta o prazo com decisões equivalentes.
        metodo_esgotado = d.acao == "preparar_consulta" and reprovacoes_objetivo.get(d.objetivo, 0) >= 2
        if (d.acao == "preparar_consulta" and pode_aprender_sozinho(user)
                and ((d.objetivo, tuple(sorted(d.parametros))) in planos_reprovados or metodo_esgotado)):
            fonte = next((o for o in objetivos if o.codigo == d.objetivo), None)
            alvo = " ".join([texto, *fonte.tabelas]) if fonte else texto
            descoberta = Decisao("", "aprender_tabela", "", (), alvo, "", False, ())
            if _assinatura(descoberta) not in feitas:
                d, assinatura = descoberta, _assinatura(descoberta)
                metodo_esgotado = False
        if metodo_esgotado:
            ac.anotar(d.acao, "metodo_reprovado")
            if ac.pode_cutucar():
                ac.cutucadas += 1
                observacoes.append(Observacao(d.acao, "metodo_reprovado", (
                    f"A consulta {d.objetivo} já foi reprovada pela revisão duas vezes nesta mensagem. Não use de novo: "
                    "mude a fonte, o campo, a chave da contagem ou o critério (aprender_tabela), ou faça UMA pergunta "
                    "objetiva sobre o significado de negócio do termo que falta (conversar terminando em ?).")))
                andamento.avisar("Essa consulta já foi reprovada; mudando a fonte ou o critério")
                continue
            causa = "revisao_reprovada" if ac.reprovada else "acao_repetida"
            break
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
        if d.acao == 'historico_cargo':
            from .planejamento import FonteInconclusiva, autorizar_fonte
            try:
                autorizar_fonte(user, provedor, 'SRA')
            except PermissaoNegada as exc:
                return Saida(str(exc), {'tipo': 'bloqueada'})
            except (FonteInconclusiva, FalhaModelo, ImproperlyConfigured):
                observacoes.append(Observacao(d.acao, ERRO_METADADOS,
                    'A fonte do histórico ficou inconclusiva. Busque outra fonte no dicionário autorizado.'))
                continue
        registrar("interpretacao", "agente", usuario=user, estado_novo=d.acao, entidade_id=atual.pk if atual else None,
                  linhas=len(d.parametros))
        reduz = _reduz_escopo(d, cobertura)
        if (not reduz and ac.contrato and d.acao in ("preparar_consulta", "executar_consulta")
                and not any(k == "agregacao" for k, _ in d.parametros)):
            _ampliar_colunas_pedidas(user, c, d, atual, ac)
        comparada = None
        try:
            if comparacao.periodos(texto) and d.acao == 'preparar_consulta' and comparacao.deste_passo(ac, d) and not reduz:
                comparada = comparacao.executar(user, c, d, defs, objetivos, texto, interrompido)
            if comparada is not None:
                d = replace(d, atende=tuple(i for i, e in enumerate(ac.contrato) if e.get('comparacao')))
            saida = reduz or comparada or _agir(user, c, d, atual, defs, texto, provedor, entrada_passo, objetivos,
                                                administrador, interrompido=interrompido, resultado_base=resultado_base)
        except ValidationError as exc:
            parametros_recusados.add(assinatura)
            observacoes.append(Observacao(d.acao, ERRO_PARAMETROS, ' '.join(exc.messages)))
            ac.anotar(d.acao, ERRO_PARAMETROS)
            continue
        except prazo.PrazoEsgotado:
            # M96: o tempo total acabou DENTRO da ação (ex.: aprendendo uma tabela nova). Antes virava erro 500 e a
            # mensagem sumia; agora segue o caminho do prazo: continua em segundo plano e responde no chat.
            ac.anotar(d.acao, TEMPO_ESGOTADO)
            causa = TEMPO_ESGOTADO if passo_programado else "prazo"
            break
        if getattr(saida, "decisao_efetiva", None) is not None:
            # M154: a descoberta criou a consulta e executou preparar_consulta com ela: é ESSA decisão que vale para a
            # receita, a revisão e o controle de repetição (antes ficava registrada como aprender_tabela e a receita
            # não era gravada; a pergunta equivalente voltava à descoberta).
            d = replace(saida.decisao_efetiva, atende=d.atende or saida.decisao_efetiva.atende)
            feitas.add(_assinatura(d))
        if passo_programado and saida.observacao and saida.observacao.situacao in TRANSITORIAS:
            # Reexecuta o plano conhecido no worker. Um passo posterior bem-sucedido não deve ocultar a falha.
            causa = saida.observacao.situacao
            ac.anotar(d.acao, causa)
            observacoes.append(saida.observacao)
            andamento.avisar("A consulta conhecida encontrou uma falha temporária; vou retomá-la")
            break
        outra = None if (reduz or comparada) else _busca_na_descricao(d)
        if outra is not None and getattr(saida.resultado, 'ok', False) and not getattr(saida.resultado, 'linhas', None):
            # M94: a busca por nome (campo de pessoa) não achou; o texto pode ser de produto/descrição
            # ("CARREGADOR APPLE USB-C 20W"). Tenta uma vez com "contém" na coluna de descrição (uma coluna só).
            andamento.avisar("Nada no nome; procurando o texto na descrição")
            saida2 = _agir(user, c, outra, atual, defs, texto, provedor, entrada_passo, objetivos, administrador,
                           interrompido=interrompido, resultado_base=resultado_base)
            if getattr(saida2.resultado, 'ok', False) and getattr(saida2.resultado, 'linhas', None):
                d, saida = outra, saida2
        if alias_inconclusivo := getattr(saida.resultado, "fonte_inconclusiva", ""):
            anteriores = entrada.planejamento.get("fontes_inconclusivas", [])
            entrada = replace(entrada, planejamento={**entrada.planejamento,
                              "fontes_inconclusivas": list(dict.fromkeys([*anteriores, alias_inconclusivo]))})
        vazia = _fonte_vazia(d, saida)
        if vazia:
            # M104: zero linhas porque a TABELA está vazia nesta instalação (ex.: CN9 contratos padrão, quando a
            # empresa usa outra rotina). Não é resposta: é fonte errada. Volta à descoberta, sem repetir essa tabela.
            andamento.avisar(f"A tabela {vazia} está vazia nesta instalação; procurando a fonte certa")
            anteriores = entrada.planejamento.get("fontes_vazias", [])
            entrada = replace(entrada, planejamento={**entrada.planejamento,
                              "fontes_vazias": list(dict.fromkeys([*anteriores, vazia]))})
            saida = Saida("", saida.ficha,
                          observacao=Observacao(d.acao, ERRO_METADADOS,
                                                f"A tabela {vazia} não tem nenhum registro nesta instalação: não é a fonte "
                                                "do assunto. Use aprender_tabela para achar outra tabela (rotina própria "
                                                "da empresa, tabela personalizada) e não use mais " + vazia + "."))
        if saida.resultado is not None and getattr(saida.resultado, 'ok', False):
            entregas.completar(texto,saida.resultado)
            if admissoes.completar(texto, saida, user):
                indices = admissoes.entregas_atendidas(ac.contrato, saida.resultado)
                d = replace(d, atende=tuple(sorted(set(d.atende) | set(indices))))
            from . import calculos
            resultado_base = saida.resultado
            entrada = replace(entrada, resultado_anterior=calculos.metadados(resultado_base))
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
            ev = coord.evidencia(d, saida, texto)
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
            if ac.contrato:
                from .planejamento import registrar_entregas
                registrar_entregas(ac, d, saida)
            if saida.resultado is not None:
                from . import revisao
                falta_rev = revisao.revisar(user, provedor, texto, ac.contrato, saida)
                if falta_rev == revisao.INDISPONIVEL:
                    ac.anotar("revisao", "modelo_falhou")
                    ac.reprovada = ""
                    causa, saida, previa = "modelo_falhou", None, None
                    break
                if falta_rev:
                    ac.reprovada, previa = falta_rev, None
                    ac.revisoes += 1
                    ac.cumpridas = []
                    ac.anotar("revisao", "entrega_nao_atende")
                    if ac.revisoes > revisao.tentativas():
                        break
                    observacoes.append(Observacao("revisao", "entrega_nao_atende", falta_rev))
                    continue
            return _fechar(ac, user, c, mensagem_id, saida, ev or coord.INVESTIGACAO, parcial=not ev)
        ev = coord.evidencia(d, saida, texto)  # o que ESTA ação entregou (antes de juntar com a etapa anterior)
        if saida.observacao is not None and saida.observacao.situacao == ERRO_PARAMETROS:
            parametros_recusados.add(assinatura)
        if ev and (d.acao in ("aprender_tabela", "criar_consulta", "ajustar_filtro", "voltar_filtro")
                   or getattr(saida, "decisao_efetiva", None) is not None):
            # M40: a consulta criada/ajustada neste passo precisa estar entre as que o modelo pode escolher.
            objetivos = objetivos_para(user)
            codigos_par = sorted({cod for o in objetivos for cod in o.parametros} | set(PARAMETROS_APRENDIDOS))
            defs = orq._definicoes(codigos_par)
            entrada = replace(entrada, objetivos=_catalogo(user, objetivos, defs),
                              aprendidos=_aprendidos(administrador))
        if ac.contrato:
            from .planejamento import registrar_entregas
            anteriores = set(ac.cumpridas)
            registrar_entregas(ac, d, saida)
            novos_indices = set(ac.cumpridas) - anteriores
            if len(ac.contrato) > 1 and novos_indices:
                try:
                    passo = aprendizado_receita.passo_verificado(d, saida, novos_indices)
                    if passo:
                        passos_verificados.append(passo)
                except (KeyError, ValidationError, TransicaoInvalida):
                    pass
            if saida.observacao and saida.observacao.recuperavel:
                observacoes.append(saida.observacao)
            if ac.pendentes() and ev not in (coord.PERGUNTA, coord.BLOQUEIO, coord.DELEGADA):
                # Sem entrega comprovada, são revisões da mesma tentativa, não novas respostas.
                if ((saida.observacao is None or not saida.observacao.recuperavel)
                        and (len(ac.contrato) == 1 or novos_indices)):
                    previa = _juntar(previa, saida) if previa is not None and ac.cumpridas else saida
                # M67: guarda a causa real (parâmetro inválido, metadado, permissão...), não só "pendente".
                ac.anotar(d.acao, saida.observacao.situacao if saida.observacao is not None
                          and saida.observacao.situacao != SUCESSO else 'entrega_pendente', ev)
                diverg = [f"{k} ({v})" for k, v in ac.divergencias.items() if k in ac.pendentes()]
                dica_passo = _dica_entregas_pendentes(ac, ev, saida)
                observacoes.append(Observacao(d.acao, coord.ETAPA, 'Ainda faltam entregas verificadas: ' + '; '.join(ac.pendentes())
                    + (('. Critério diferente do pedido, refaça com o período/filial da entrega: ' + '; '.join(diverg))
                       if diverg else '')
                    + (f'. {dica_passo}' if dica_passo else '')))
                if ac.exig in (coord.VERIFICACAO, coord.CALCULO) and len(ac.contrato) == 1 and ev == coord.CONSULTA:
                    observacoes.append(Observacao(d.acao, coord.ETAPA, ac.falta_obs(_consulta_recente(user, c))))
                if diverg:
                    andamento.avisar('O critério usado não é o pedido; refazendo com o período/filial certos')
                entrada = replace(entrada, planejamento={**entrada.planejamento, 'pendentes': ac.pendentes(), 'cumpridas': ac.cumpridas})
                atual = _ativa(user, c)
                continue
        if (previa is not None and not comparacao.ranking(texto)
                and not (ev == coord.VERIFICACAO and coord.ano_admissoes_e_desligamentos(texto)
                         and not any(getattr(r, 'titulo_entrega', '') for r in _blocos(previa.resultado)))
                and (not comparacao.contagens_mensais(texto)
                     or any(getattr(r, 'titulo_entrega', '') for r in _blocos(previa.resultado)))):
            saida = _juntar(previa, saida)
        ac.anotar(d.acao, saida.observacao.situacao if saida.observacao else ("ok" if ev else "sem_resultado"), ev)
        if d.acao == "lembrar":
            ac.memoria_feita = bool(saida.ficha.get("memoria_registrada"))
            if ac.entregue and ac.memoria_feita and ac.partes_faltam <= 0 and ev == coord.ACAO:
                ev = ac.entregue  # a resposta trouxe dados, então ainda precisa da revisão abaixo
        if ac.cumprido(ev) and d.acao != "lembrar":
            if ac.contrato and not ac.pendentes() and len(ac.contrato) >= coord.partes(texto):
                ac.partes_faltam = 0
            elif ac.partes_faltam < 0:
                ac.partes_faltam = coord.partes(texto) - 1
            elif ac.partes_faltam > 0 and ac.entregue:
                ac.partes_faltam -= 1
        if (ac.cumprido(ev) and coord.pede_memoria(texto) and not ac.memoria_feita and d.acao != "lembrar"
                and ac.partes_faltam <= 0):
            # M40: "me dê o valor e guarde isso": a resposta saiu, mas o ensinamento ainda não foi guardado.
            if not ac.pode_cutucar():
                causa = "passos"
                break
            ac.cutucadas += 1
            ac.entregue, previa = ev, saida
            observacoes.append(Observacao(d.acao, coord.ETAPA, coord.FALTA_MEMORIA))
            andamento.avisar("Resposta pronta; agora guardando o que você ensinou")
            continue
        if ac.entregue and ev == coord.RESPOSTA and previa_parte is not None:
            # M41: depois da 1ª resposta, texto sem consulta não comprova a outra pergunta ("já rodei as duas"):
            # ficam só as respostas já conferidas, sem a afirmação do modelo.
            return _encerrar_sem_cumprir(ac, user, c, mensagem_id, previa, previa, "passos", observacoes)
        if (ac.partes_faltam > 0 and (ac.cumprido(ev) or (d.acao == "lembrar" and ac.memoria_feita))
                and ev not in (coord.PERGUNTA, coord.BLOQUEIO, coord.DELEGADA)):
            # M41: "quantos entraram? e quantos saíram?": a primeira resposta saiu; falta a outra pergunta.
            if not ac.pode_cutucar():
                causa = "passos"
                break
            ac.cutucadas += 1
            ac.entregue = ac.entregue or ev
            if previa_parte is None:
                previa_parte = saida
            previa = saida
            feita = (f"“{OBJETIVOS[d.objetivo].nome}” com " + ", ".join(f"{k}={v}" for k, v in d.parametros)
                     if d.objetivo in OBJETIVOS else d.acao)
            observacoes.append(Observacao(d.acao, coord.ETAPA, coord.falta_parte(texto, [feita])))
            andamento.avisar("Uma pergunta respondida; agora a próxima")
            continue
        if (ev and ev not in (coord.PERGUNTA, coord.BLOQUEIO, coord.DELEGADA, coord.ACAO)
                and (ac.entregue or ac.cumprido(ev))):
            # M109: a resposta está montada, mas ainda não foi enviada: a própria IA confere se atende ao pedido.
            from . import revisao
            from .contagem import entregas_separadas
            separadas = entregas_separadas(texto)
            titulos = [getattr(r, 'titulo_entrega', '') for r in _blocos(saida.resultado)]
            comprovadas = (separadas and ac.contrato == separadas and not ac.pendentes()
                           and sorted(titulos) == sorted(e['descricao'] for e in separadas))
            preservadas = set()
            falta_rev = "" if comprovadas else revisao.revisar(user, provedor, texto, ac.contrato, saida, preservadas=preservadas)
            if falta_rev == revisao.INDISPONIVEL:
                ac.anotar("revisao", "modelo_falhou")
                ac.reprovada = ""
                causa, saida, previa = "modelo_falhou", None, None
                break
            if falta_rev:
                if receita_id:
                    aprendizado_receita.descartar(receita_id)
                    receita_id, receita_d, fila_receita = None, None, []
                if d.acao == "preparar_consulta":
                    planos_reprovados.add((d.objetivo, tuple(sorted(d.parametros))))
                    reprovacoes_objetivo[d.objetivo] = reprovacoes_objetivo.get(d.objetivo, 0) + 1
                preservadas.intersection_update(ac.cumpridas)
                passos_verificados = [p for p in passos_verificados if set(p["atende"]) <= preservadas]
                comprovada = None  # M155: só os blocos que a revisão aprovou (ex.: o total), nunca os reprovados
                titulos_validos = {ac.contrato[i]["descricao"] for i in preservadas}
                for bloco in _blocos(saida.resultado):
                    if getattr(bloco, "titulo_entrega", "") in titulos_validos:
                        parte = Saida("", {}, resultado=bloco)
                        comprovada = _juntar(comprovada, parte) if comprovada else parte
                pergunta = pergunta_de_definicao(falta_rev)
                if pergunta:
                    # M154: a revisão concluiu que falta uma definição de negócio que só a pessoa sabe (ex.: o que a
                    # operação chama de "deslocado"). Pergunta já, enquanto ajuda. M155: o que já foi comprovado
                    # (ex.: o total) vai junto; nada reprovado aparece. A resposta curta retoma este pedido.
                    ac.anotar("revisao", "definicao_pendente")
                    duvida = Saida(pergunta, {"tipo": "esclarecimento", "pedido_original": texto[:1000]})
                    if comprovada is None and saida.resultado is not None and isinstance(falta_rev, revisao.ClassificacaoPendente):
                        # A lacuna é só a definição: o levantamento (ex.: contagem por situação, com o nome de cada
                        # uma) é o que a pessoa precisa ver para responder. Vai junto, sem afirmar a classificação.
                        comprovada = Saida("Levantamento até aqui (ainda sem a classificação pedida):", {},
                                           resultado=saida.resultado, solicitacao=saida.solicitacao)
                    return _fechar(ac, user, c, mensagem_id,
                                   _juntar(comprovada, duvida) if comprovada else duvida, coord.PERGUNTA)
                ac.revisoes += 1
                ac.reprovada = falta_rev
                previa_comprovada = comprovada
                andamento.avisar("A resposta não atendia ao pedido; tentando de outro jeito")
                if ac.revisoes > revisao.tentativas():
                    causa = "revisao_reprovada"
                    break
                ac.cumpridas, ac.entregue, previa = sorted(preservadas), "", comprovada
                ac.anotar("revisao", "entrega_nao_atende", "")
                observacoes.append(Observacao("revisao", "entrega_nao_atende", falta_rev))
                entrada = replace(entrada, planejamento={**entrada.planejamento, 'pendentes': ac.pendentes(),
                                                         'cumpridas': ac.cumpridas,
                                                         'ultima_revisao': {
                                                             'objetivo': d.objetivo,
                                                             'colunas_obtidas': [b['colunas'] for b in revisao.descrever(saida)],
                                                             'correcao': falta_rev}})
                atual = _ativa(user, c)
                continue
            if ac.reprovada:
                _aprender_revisao(user, texto, ac.reprovada)
            ac.reprovada = ""
            if not ac.pendentes():
                try:  # M125: resposta conferida: a lógica vira receita reutilizável (sem dados do ERP)
                    if len(ac.contrato) > 1:
                        aprendizado_receita.guardar_composta(user, texto_receita, ac.contrato, passos_verificados)
                    else:
                        aprendizado_receita.guardar(user, texto, d, saida, ac.contrato, ev)
                except Exception:
                    import logging
                    logging.getLogger(__name__).warning("receita não gravada", exc_info=True)
            if comprovadas:
                if len(separadas) == 1:
                    quantidade = saida.resultado.totais.medidas[0].valor
                    saida.resultado.curta_salva = f"{quantidade} {separadas[0]['descricao'].split()[0].lower()}"
                    ac.formato = "direto"
                else:
                    from .contagem import detalhe_eventos_separados, resumo_lado_a_lado
                    from .diagnostico import Bloco, Diagnostico
                    por_titulo = {r.titulo_entrega: r for r in _blocos(saida.resultado)}
                    resumo = resumo_lado_a_lado([por_titulo[e["descricao"]] for e in separadas],
                                                ["Admitidos", "Desligados"])
                    if resumo is not None:
                        detalhes = detalhe_eventos_separados(user, c, separadas, objetivos, defs, resumo.escopo)
                        n_adm, n_des = resumo.linhas[-1][1:]
                        mostrar_filiais = separadas[0]["detalhar_filiais"]
                        if not mostrar_filiais:
                            resumo.linhas = resumo.linhas[-1:]
                        introducao = (f"Em {separadas[0]['ano_evento']}, foram {n_adm} admissões e "
                                      f"{n_des} desligamentos nas filiais consultadas.")
                        saida.texto = ""
                        blocos = ([Bloco("Detalhes dos vínculos", detalhes)] if detalhes else [])
                        if detalhes is None:
                            introducao += " Os detalhes nominais ficaram indisponíveis para esta consulta."
                        resumo.premissas = ["Admissões pela data de admissão. Desligamentos pela data de desligamento."]
                        blocos.append(Bloco("Resumo por filial" if mostrar_filiais else "Resumo do período", resumo))
                        saida.resultado = Diagnostico(blocos, resumo=introducao)
                        saida.ficha["tipo"] = "calculo_resultado"
                        saida.solicitacao = None  # repetir um único evento não refaz os dois
            elif (len(ac.contrato) > 1 and not ac.pendentes() and all(
                    e.get("metrica") == "contagem" and e.get("tipo") == coord.CALCULO for e in ac.contrato)):
                from .contagem import resumo_lado_a_lado, resumo_situacoes, rotulos_curtos
                blocos = _blocos(saida.resultado)
                titulos = [getattr(r, "titulo_entrega", "") for r in blocos]
                rotulos = rotulos_curtos(titulos) if all(titulos) else titulos
                resumo = resumo_lado_a_lado(blocos, rotulos) or resumo_situacoes(blocos, rotulos)
                if resumo is not None and len(blocos) == len(ac.contrato):
                    saida.resultado = resumo
                    saida.texto = ""
                    saida.ficha["tipo"] = "calculo_resultado"
                    saida.solicitacao = None
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
            observacoes.append(Observacao(d.acao, coord.ETAPA, ac.falta_obs(_consulta_recente(user, c))))
            andamento.avisar("Etapa concluída; ainda falta parte do pedido" if ac.exig != coord.VERIFICACAO
                             else "Lista pronta; agora vou verificar cada pessoa")
            atual = _ativa(user, c)
            continue
        causa = saida.observacao.situacao if saida.observacao is not None else "sem_resultado"
        break
    if ac.reprovada:
        # M109: a última resposta montada foi reprovada pelo revisor: nada dela vai para a pessoa; vai para a
        # manutenção com o que faltava (que corrige e refaz o pedido, que passa pelo revisor de novo).
        # M155: o que a revisão já aprovou (ex.: o total) é entregue junto com o problema concreto que falta resolver.
        causa, saida, previa = "revisao_reprovada", Saida("", {}), previa_comprovada
        ac.capacidade = ac.capacidade or ac.reprovada[:300]
    return _encerrar_sem_cumprir(ac, user, c, mensagem_id, saida, previa, causa, observacoes)


def _aprender_revisao(user, texto, falta):
    """M110: a revisão reprovou uma resposta e a nova tentativa passou: guarda na memória como corrigir, para o
    próximo pedido parecido já sair certo. Administrador guarda para a empresa; os demais, para si (memoria.lembrar)."""
    como = falta.split("Como corrigir:", 1)[-1].strip() if "Como corrigir:" in falta else falta
    try:
        memoria.lembrar(user, f"Aprendido na revisão: em pedidos como “{' '.join(texto.split())[:110]}”, {como}"[:300],
                        tipo="correcao")
        andamento.avisar("Aprendi como responder esse tipo de pedido; guardei na memória")
    except Exception:
        pass  # aprender nunca impede a resposta


PASSAGEIRAS = ("prazo", "passos", "modelo_falhou", TEMPO_ESGOTADO, FALHA_TRANSITORIA)


def _fechar(ac, user, c, mensagem_id, saida, ev, parcial=False) -> Saida:
    """Pedido encerrado por evidência (cumprido), por pergunta à pessoa ou por impedimento real."""
    from . import objetivos as coord
    from .models import SituacaoObjetivo as SO

    if (not parcial and ev not in (coord.DELEGADA, coord.PERGUNTA, coord.BLOQUEIO)
            and (ac.pendentes() or ac.partes_faltam > 0 or (coord.pede_memoria(ac.texto) and not ac.memoria_feita))):
        return _encerrar_sem_cumprir(ac, user, c, mensagem_id, saida, None, "passos", [])
    situacao = (SO.TAREFA if ev == coord.DELEGADA else SO.AGUARDANDO if ev == coord.PERGUNTA else SO.IMPEDIDO if (ev == coord.BLOQUEIO or parcial)
                else SO.CUMPRIDO)
    if mensagem_id is not None and (ac.registro is not None or len(ac.trilha) > 1 or situacao != SO.CUMPRIDO):
        o = coord.registrar(ac, user, c, mensagem_id, situacao,
                            "investigação incompleta" if parcial else (
                                "fontes sem intervalo comprovável" if saida.ficha.get('tipo') == 'impedimento_dados'
                                else "sem permissão" if ev == coord.BLOQUEIO else ""),
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
    tem_insumo = any(
        getattr(r, 'ok', False) or getattr(r, 'linhas', None) or getattr(r, 'consolidado', False)
        for r in (getattr(saida, 'resultado', None), getattr(previa, 'resultado', None)))
    if ac.pendentes() or tem_insumo:
        # Resultados usados como insumo ainda não passaram pela revisão da resposta completa.
        saida, previa = None, None
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
    falta = ("Ainda faltam perguntas desta mensagem sem resposta comprovada" if ac.partes_faltam > 0
             else "O ensinamento pedido ainda não foi guardado" if coord.pede_memoria(ac.texto) and not ac.memoria_feita
             else coord.FALTA[exig])
    if ac.contrato and ac.pendentes():
        # M142 (pedido de Diego): sem a lista "Seu pedido tem N partes / Ainda falta". Era o aviso da manutenção em
        # segundo plano, que foi desligada. O que faltou fica no registro do objetivo, para a TI.
        falta = "Não terminei esta resposta"
    if exig == coord.VERIFICACAO:
        ficha["verificacao_pendente"] = True
    registro = ac.registro
    feitas = registro.continuacoes if registro is not None else 0
    if causa not in PASSAGEIRAS and _classe_sem_codigo(ac) in (TEMPO_ESGOTADO, FALHA_TRANSITORIA):
        # M80: o ERP demorou/falhou de passagem e a IA repetiu a mesma ação: é lentidão, não pedido impossível.
        # Tenta de novo sozinha em segundo plano (com espera), em vez de pedir para a pessoa repetir.
        causa = _classe_sem_codigo(ac)
    classe = _classe_sem_codigo(ac)
    calculo_pendente = ac.exig == coord.CALCULO or any(
        e.get("tipo") == "calculo" and i not in ac.cumpridas for i, e in enumerate(ac.contrato))
    # Um prazo esgotado durante investigação pode ser transitório. Critérios recusados e um ciclo
    # estagnado depois de executar consultas apontam para um defeito reproduzível no atendimento.
    consultou = any(p.endswith((":consulta", ":calculo")) for p in ac.trilha)
    if (causa in ("prazo", "passos") and calculo_pendente
            and (classe == ERRO_PARAMETROS or (not classe and consultou and ac.estagnado()))):
        causa = "calculo_pendente"
    andando = not ac.estagnado() or causa in (TEMPO_ESGOTADO, FALHA_TRANSITORIA, "modelo_falhou", "prazo")
    if (causa in PASSAGEIRAS and andando and mensagem_id is not None and feitas < int(coord.cfg()["continuacoes"])
            and not (registro is not None and registro.retomado and causa == "passos")):
        o = coord.registrar(ac, user, c, mensagem_id, SO.CONTINUAR, f"continua: {causa}",
                            espera_s=int(coord.cfg()["espera_s"]))
        ponto = "" if "\n" in falta or falta.endswith((".", ":")) else "."
        final = Saida(f"{texto}\n\n{falta}{ponto}\nSigo trabalhando nas partes que faltam, em segundo plano. A resposta completa "
                      "aparece aqui, sem você precisar pedir de novo."
                      .strip(), {**ficha, "tipo": ficha.get("tipo") or "objetivo", "objetivo": o.pk})
        registrar("modelo_falhou", "agente", usuario=user, estado_novo="objetivo_continua", resultado="erro")
    elif (causa not in PASSAGEIRAS and causa != "revisao_reprovada" and (classe := _classe_sem_codigo(ac))
          and classe == SEM_PERMISSAO):
        # M116: só a falta de permissão (limite real por função) encerra sem correção. Critério recusado (M110),
        # tabelas que não serviram e metadado inesperado vão para a correção automática (else abaixo); nunca
        # "reformule o pedido".
        # M67 (auditoria 17/11, item 11): a falha tem causa conhecida que não é falta de código — escolha ou
        # parâmetro do modelo, metadado/ERP indisponível ou permissão. Não abre manutenção.
        if mensagem_id is not None:
            o = coord.registrar(ac, user, c, mensagem_id, SO.IMPEDIDO, f"sem evidência: {classe}")
            ficha["objetivo"] = o.pk
        motivo = {ERRO_PARAMETROS: "os critérios que montei não são aceitos por essa consulta",
                  ERRO_METADADOS: "o dicionário/ERP não respondeu como esperado",
                  TEMPO_ESGOTADO: "o ERP não respondeu a tempo",
                  FALHA_TRANSITORIA: "o ERP ou a IA tiveram uma falha passageira",
                  SEM_PERMISSAO: "parte do pedido está fora das suas permissões"}[classe]
        if classe == ERRO_METADADOS and tentativas:
            motivo = next((o.detalhe for o in reversed(tentativas) if o.situacao == classe and o.detalhe), motivo)
        final = Saida(f"{texto}\n\n{falta}. Parei porque {motivo}; isso não é falta de função no sistema, então não "
                      "abri correção de código. Tente de novo ou reformule o pedido.".strip(), ficha)
    elif causa in PASSAGEIRAS and not (causa in ("prazo", "passos") and ac.pendentes()
                                        and _classe_sem_codigo(ac) in ("", ERRO_PARAMETROS)):
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
        esgotou_correcoes = registro is not None and (
            coord.correcoes_publicadas(registro) >= coord.MAX_CORRECOES_POR_PEDIDO)
        from iativa.manutencao.incidentes import automatica
        # M133: manutenção desligada (pedido de Diego) = nenhum registro de correção; a IA entrega ou explica.
        inc = None if esgotou_correcoes or not automatica() else coord.encaminhar_manutencao(ac, causa, tabela)
        retomado = registro is not None and registro.retomado
        # M61: só espera a manutenção se ela vai mesmo atuar agora; incidente bloqueado ou esgotado não deixa o
        # pedido parado "na fila" para sempre.
        from iativa.manutencao.incidentes import elegivel as _vai_corrigir
        # M113: revisão reprovada depois de uma correção volta à manutenção (o ciclo segue até a resposta certa);
        # os limites de tentativas/reaberturas do incidente e o diário da manutenção evitam laço sem fim.
        situacao = (SO.MANUTENCAO if (inc is not None and mensagem_id is not None
                                      and (not retomado or causa == "revisao_reprovada" or bool(ac.pendentes()))
                                      and (_vai_corrigir(inc) or inc.situacao == 'em_correcao'))
                    else SO.IMPEDIDO)
        if mensagem_id is not None:
            o = coord.registrar(ac, user, c, mensagem_id, situacao, f"sem evidência: {causa}", incidente=inc)
            ficha["objetivo"] = o.pk
        tentei = coord.resumo_tentativas(ac)
        if situacao == SO.MANUTENCAO:
            # M48: fala como uma IA de verdade: avisa que está corrigindo e volta com a resposta sozinha.
            ficha["corrigindo"] = True
            # M49: sem resultado conferido, o texto do modelo não aparece (pode trazer um número não calculado).
            if saida is None or (saida.resultado is None and (saida.observacao is None
                                                               or saida.observacao.acao == "conversar")):
                texto = ""
            # M53: diz O QUE vai ser implementado, quando o modelo declarou.
            oque = f" Vou implementar: {inc.condicoes.get('capacidade')}." if (inc.condicoes or {}).get("capacidade") else ""
            final = Saida(f"{texto}\n\nNão tenho isso ainda, corrigindo…\n{falta}\n{oque.strip()} Estou ajustando o sistema "
                          f"(registro nº {inc.pk}) para entregar a sua resposta. Assim que "
                          "a correção entrar no ar, refaço o pedido e respondo aqui, sem você precisar perguntar de "
                          "novo. Você acompanha cada etapa aqui.", ficha)
        else:
            from iativa.manutencao.incidentes import AVISAR_TI
            aviso = (" Isso já tinha sido encaminhado à manutenção e voltou a acontecer; ficou para análise de uma "
                     "pessoa da TI." if retomado else
                     " " + AVISAR_TI.format(n=inc.pk) if inc is not None and not automatica() else "")
            if ac.reprovada and causa == "revisao_reprovada":
                lacuna, _, orientacao = ac.reprovada.partition(" Como corrigir:")
                lacuna = lacuna.removeprefix("A resposta montada não atende: ").rstrip(".")
                pergunta = pergunta_de_definicao(ac.reprovada)
                if pergunta:
                    final = Saida(pergunta, {**ficha, "tipo": "esclarecimento", "pedido_original": ac.texto[:1000]})
                else:
                    # M155: nunca devolver só a lista do que foi pedido: diz o problema concreto encontrado pela
                    # revisão e separa a falha técnica real (validador, ERP) da falta de dado ou definição.
                    problema = orientacao.strip().rstrip(".") or lacuna
                    tecnica = next((o.detalhe for o in reversed(observacoes or [])
                                    if o.situacao in (ERRO_METADADOS, ERRO_PARAMETROS)
                                    and ("validador" in o.detalhe or "recusad" in o.detalhe)), "")
                    texto_final = f"Ainda não consegui responder com segurança. O que falta resolver: {problema}."
                    if tecnica:
                        texto_final += (" Uma das tentativas parou numa falha técnica do sistema ("
                                        + tecnica.rstrip(".")[:160] + "); isso não é falta de dado nem de definição.")
                    final = Saida(texto_final + aviso, {**ficha, "tipo": "impedimento_dados"})
            else:
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
        ac = coord.Acompanhamento(o.mensagem.texto, exig=o.exigencia, origem=o.origem, registro=o,
                                 contrato=o.contrato.get('entregas', []),
                                 cumpridas=[])
        feito = ", ".join(o.trilha[-6:]) or "nada concluído"
        obs = [Observacao("continuacao", "continuacao",
                          f"Continuação automática do MESMO pedido (a pessoa não mandou nova mensagem). Já tentado "
                          f"(ação:situação:evidência): {feito}. {coord.FALTA.get(o.exigencia, '')}: {coord.COMO.get(o.exigencia, '')}.")]
        from . import tempos
        try:
            with tempos.pedido("retomada" if o.retomado else "continuacao") as t:
                saida = conversar(user, c, o.mensagem.texto, mensagem_usuario_id=o.mensagem_id, acomp=ac,
                                  observacoes=obs, interrompido=interrompido,
                                  contexto_id=o.mensagem.ficha.get("mensagem_origem"))
            # M125: tempo desta continuação e o total desde a mensagem da pessoa (fila, manutenção e refazer).
            from django.utils import timezone
            saida.ficha = {**(saida.ficha or {}), "tempos_ms": {
                **t, "desde_o_pedido": int((timezone.now() - o.mensagem.criado_em).total_seconds() * 1000)}}
            if saida.resultado is not None and getattr(saida.resultado, "ok", True):
                saida.ficha["mensagem_origem"] = o.mensagem.ficha.get("mensagem_origem") or o.mensagem_id
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
        texto = saida.texto if saida.ficha.get("corrigindo") else prefixo + saida.texto
        msg = Mensagem.objects.create(conversa=c, papel=Papel.IA, texto=texto[:4000], ficha=saida.ficha,
                                     solicitacao=saida.solicitacao)
        from iativa.chat.resultados import guardar
        guardar(msg, saida.resultado)
        return msg


# M25: pedidos sobre a lista inteira geram obrigação de cobertura verificada pelo backend.
INVESTIGAR_RE = re.compile(r"\b(todos|todas|cada|demais|lista|confir|verific|confer|chec|algum|alguma|readmit|"
                           r"vinculo|mesmo)\w*")
COBERTURA_RE = re.compile(r"\b(todos|todas|cada um|cada uma|demais|a lista|da lista|na lista|essa lista|esses|"
                          r"essas|eles|elas|o resto)\b")


VERIFICAR_RE = re.compile(r"\b(verifi|confir|confer|chec|investig|descubr)\w*")
REFERENCIA_RE = re.compile(r"\b(deles|delas|eles|elas|esses|essas|cada|todos|todas|lista|demitid|desligad|admitid|"
                           r"readmit|matricula|vinculo|funcionari|pessoas)\w*")


def _classe_sem_codigo(ac) -> str:
    """M67: causa conhecida que não é defeito de código, pela trilha do pedido (a mais grave primeiro)."""
    # Só passos que tocaram dados contam; "conversar" carrega a observação de um passo anterior.
    situacoes = [x.split(":")[1] for x in ac.trilha if x.count(":") == 2
                 and x.split(":")[0] not in ("conversar", "continuacao", "falta_capacidade", "-")]
    if SEM_PERMISSAO in situacoes:
        return SEM_PERMISSAO
    # Uma falha de descoberta antiga não explica um cálculo inválido depois de uma consulta executada.
    for situacao in reversed(situacoes):
        if situacao in (ERRO_METADADOS, TEMPO_ESGOTADO, FALHA_TRANSITORIA, ERRO_PARAMETROS):
            return situacao
        if situacao in (SUCESSO, "ok", "entrega_pendente"):
            break
    return ""


def _juntar(previa: "Saida", saida: "Saida") -> "Saida":
    """A resposta final inclui a etapa anterior (ex.: a lista que foi mostrada) e o passo seguinte."""
    entregue = [r for r in _blocos(previa.resultado) + _blocos(saida.resultado) if getattr(r, 'titulo_entrega', '')]
    if saida.resultado is None:
        saida.resultado = previa.resultado
    elif len({id(r) for r in entregue}) > 1:
        # M68: cada entrega comprovada aparece com a sua tabela (antes só a última aparecia e a lista sumia).
        from .diagnostico import Bloco, Diagnostico
        unicos = list({id(r): r for r in entregue}.values())
        saida.resultado = Diagnostico([Bloco(r.titulo_entrega, r) for r in unicos], '')
    elif entregue and not getattr(saida.resultado, 'titulo_entrega', ''):
        # A tabela deste passo não cumpriu nada; a entregue continua sendo a resposta (não troca pelo que não foi pedido).
        saida.resultado = entregue[0]
    elif previa.resultado is not None and previa.resultado is not saida.resultado:
        # M41: só a última tabela aparece; o número já conferido da etapa anterior vai no texto.
        anterior = getattr(previa.resultado, "mensagem", "")
        if anterior and anterior not in previa.texto:
            previa.texto = f"{previa.texto}\n\n{anterior}".strip()
    # O "vou fazer" das etapas anteriores já foi feito; o do último passo (ex.: tarefa agendada) fica.
    anteriores = [p for p in previa.texto.split('\n\n') if not _INTENCAO.search(p)]
    saida.texto = '\n\n'.join(dict.fromkeys(p.strip() for p in
        anteriores + saida.texto.split('\n\n') if p.strip()))
    if saida.solicitacao is None:
        saida.solicitacao = previa.solicitacao
    return saida


# M68: "Agora vou buscar a lista..." é andamento (já apareceu enquanto trabalhava), não parte da resposta.
_INTENCAO = re.compile(r'\b(agora|em seguida|depois|a seguir|na sequencia|na sequência)\b[^.]*\bvou\b'
                       r'|\bvou (buscar|consultar|listar|calcular|conferir|verificar|levantar|trazer)\b', re.I)


def _blocos(res):
    if res is None:
        return []
    if getattr(res, 'consolidado', False):
        return [b.resultado for b in res.blocos if b.resultado is not None]
    return [res]


def _lista_vista(user, c):
    """Última lista concluída nesta conversa (a que a pessoa está vendo)."""
    s = c.solicitacoes.filter(usuario=user, estado=E.CONCLUIDA).exclude(objetivo="").order_by("-id").first()
    return s if s is not None and s.objetivo in OBJETIVOS and OBJETIVOS[s.objetivo].modelo_id else None


def _ampliar_colunas_pedidas(user, c, d, atual, ac) -> None:
    """M144: antes de executar uma lista de consulta aprendida, garante na consulta as colunas pedidas no contrato."""
    pedidos = []
    for i, item in enumerate(ac.contrato):
        if i not in ac.cumpridas and item.get("tipo") == "consulta" and item.get("campos_pedidos"):
            pedidos.extend(item["campos_pedidos"])
    if not pedidos:
        return
    anterior = _ultima(user, c) if atual is None else None
    objetivo = d.objetivo or (atual.objetivo if atual else (anterior.objetivo if anterior else ""))
    if not objetivo or objetivo not in OBJETIVOS or not getattr(OBJETIVOS[objetivo], "modelo_id", None):
        return
    from . import entregas
    from .models import ConsultaModelo, SituacaoModelo

    cm = ConsultaModelo.objects.filter(codigo=objetivo, situacao=SituacaoModelo.APROVADA).first()
    if cm is not None:
        entregas.garantir_campos(user, cm, pedidos)


def _dica_entregas_pendentes(ac, ev: str, saida) -> str:
    """M144: orienta o próximo passo quando parte do contrato de entregas já foi cumprida ou faltou coluna."""
    from . import entregas
    from . import objetivos as coord

    res = getattr(saida, "resultado", None)
    pendentes_itens = [item for i, item in enumerate(ac.contrato) if i not in ac.cumpridas]
    if ev == coord.CONSULTA and res is not None and getattr(res, "ok", False):
        cols = getattr(res, "colunas_meta", None) or getattr(res, "colunas", [])
        faltam_cols = []
        for item in pendentes_itens:
            if item.get("tipo") == coord.CONSULTA and item.get("campos_pedidos"):
                faltam_cols.extend(entregas.faltam(cols, item["campos_pedidos"]))
        if faltam_cols:
            nomes = ", ".join(dict.fromkeys(faltam_cols))
            return (f"Faltam colunas pedidas na lista ({nomes}); use aprender_tabela/criar_consulta com junção "
                    "para incluir esses campos, sem repetir a mesma consulta")
        if any(item.get("tipo") == coord.CALCULO for item in pendentes_itens):
            return ("A lista já foi obtida e guardada; execute agora preparar_consulta com o parâmetro agregacao "
                    "(ex.: contagem:* ou soma:CAMPO, mantendo filial-analise e filtros) para cumprir o cálculo pendente")
    if ev == coord.CALCULO and any(item.get("tipo") == coord.CONSULTA for item in pendentes_itens):
        return ("O cálculo já foi obtido e guardado; execute agora preparar_consulta sem o parâmetro agregacao "
                "para trazer a lista nominal pendente")
    return ""


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


_REFERE_ANTERIOR_RE = re.compile(
    r"^e\b|\b(?:disso|disto|desse|dessa|deste|desta|desses|dessas|destes|destas|nisso|nesse|nessa|neste|nesta|"
    r"nesses|nessas|isso|isto|dele|dela|deles|delas|acima|anterior|anteriores|mesm[oa]s?|ai|la)\b")
_PERGUNTA_DADOS_RE = re.compile(r"\b(?:quant[oa]s?|qual|quais|quem|liste|listar|lista|mostre|mostrar|traga|trazer)\b")
_AJUSTE_RE = re.compile(r"\b(?:apenas|somente|so)\b")


def pergunta_nova(c, texto: str) -> bool:
    """M152: calcular sobre o resultado anterior só vale quando a mensagem se refere a ele ("some isso", "e a média?",
    "desse total"). Pergunta completa (5+ palavras) sem referência, ou repetida na conversa, pede dados atuais: o
    resultado antigo pode estar errado ou desatualizado e nunca é reapresentado como resposta nova."""
    from . import objetivos as coord

    t = coord._norm(texto or "").strip(" ?!.")
    if not t or _REFERE_ANTERIOR_RE.search(t):
        return False
    anteriores = [coord._norm(m.texto or "").strip(" ?!.")
                  for m in c.mensagens.filter(papel="usuario").order_by("-id")[:20]]
    if anteriores.count(t) >= (2 if anteriores and anteriores[0] == t else 1):
        return True
    # Pergunta de dados com sujeito próprio ("quantos veículos tem a empresa?"); "me diga apenas os dias úteis" é ajuste.
    return len(t.split()) >= 5 and bool(_PERGUNTA_DADOS_RE.search(t)) and not _AJUSTE_RE.search(t)


_TECNICO_RE = re.compile(r"\b(?:tabela|campo|coluna|sql|alias|dicionario|sx[23]|[a-z][a-z0-9]{1,2}_[a-z0-9]+)\b")


def pergunta_de_definicao(falta_rev: str) -> str:
    """M154: a correção da revisão é UMA pergunta de negócio (termina em ?, sem pedir tabela/campo/SQL)."""
    _, _, orientacao = (falta_rev or "").partition(" Como corrigir:")
    orientacao = orientacao.strip()
    if not orientacao.endswith("?") or orientacao.count("?") != 1:
        return ""
    # M155: só a frase da pergunta; o diagnóstico antes dela é para o sistema, não para o funcionário.
    pergunta = re.split(r"(?<=[.!])\s+", orientacao)[-1].strip()
    if len(pergunta) > 300:
        return ""
    from . import objetivos as coord
    return "" if _TECNICO_RE.search(coord._norm(pergunta)) else pergunta


def pedido_esclarecido(c, texto: str, antes_de=None) -> str:
    """M154: resposta curta a uma pergunta de esclarecimento da IA retoma o pedido original: o texto passa a ser o
    pedido + o esclarecimento. Pergunta nova e completa segue como pedido próprio. O esclarecimento fica só nesta
    conversa (não vira regra da empresa)."""
    ultima = c.mensagens.filter(papel="ia")
    if antes_de is not None:
        ultima = ultima.filter(pk__lt=antes_de)
    ultima = ultima.order_by("-id").first()
    ficha = (ultima.ficha or {}) if ultima is not None else {}
    original = str(ficha.get("pedido_original") or "").strip()
    if ficha.get("tipo") != "esclarecimento" or not original or not (texto or "").strip():
        return texto
    from . import objetivos as coord
    t = coord._norm(texto).strip(" ?!.")
    if t == coord._norm(original).strip(" ?!.") or (len(t.split()) > 25 or pergunta_nova(c, texto)):
        return texto
    return (f"{original}\nEsclarecimento da pessoa sobre “{(ultima.texto or '').strip()[:300]}”: {texto.strip()}")


ROTULO_ACAO = {"investigar_lista":"verificar a lista inteira", "preparar_consulta": "consultar", "executar_consulta": "executar a consulta",
               "aprender_tabela": "procurar a tabela no dicionário", "criar_consulta": "criar uma consulta nova",
               "lembrar": "guardar na memória"}


def _agir(user, c, d, atual, defs, texto, provedor, entrada, objetivos, administrador, interrompido=None,
          resultado_base=None) -> Saida:
    """Executa UMA ação já autorizada e devolve a saída com a observação do que aconteceu."""
    ficha = {"provedor": provedor.nome, "opcoes": list(d.opcoes)}

    acao = d.acao
    if acao == 'calcular_resultado':
        from . import calculos
        if resultado_base is None and entrada.resultado_anterior and pergunta_nova(c, texto):
            # M152: "quantos veículos tem a empresa?" repetida somou a tabela errada da resposta anterior (3.336).
            motivo = ("A mensagem é uma pergunta nova e completa; não se refere ao resultado anterior. Não calcule "
                      "sobre a resposta antiga: execute a consulta de novo (preparar_consulta) com dados atuais.")
            return Saida("Pergunta nova: não reaproveitei o resultado anterior.", ficha,
                         observacao=Observacao(acao, ERRO_PARAMETROS, motivo))
        try:
            plano = calculos.operacao_exigida(d.calculo, texto)
            if (plano.get('operacao') in ('dias_uteis', 'dias_corridos') and not plano.get('campo')
                    and not plano.get('mensagem') and resultado_base is None and calculos.intervalo_do_texto(texto)):
                return Saida('Calculei sobre as datas informadas.', {**ficha, 'tipo': 'calculo_resultado'},
                             resultado=calculos.entre_datas(texto, plano['operacao']))
            if resultado_base is None and not plano.get('mensagem') and entrada.resultado_anterior:
                plano = {**plano, 'mensagem': entrada.resultado_anterior['mensagem']}
            resultado = calculos.executar(user, c, plano, resultado_base, texto=texto)
        except PermissaoNegada as exc:
            return Saida(str(exc), ficha, observacao=Observacao(acao, SEM_PERMISSAO, 'cálculo não autorizado'))
        except ValidationError as exc:
            return Saida(' '.join(exc.messages), ficha, observacao=Observacao(acao, ERRO_PARAMETROS, ' '.join(exc.messages)))
        return Saida('Calculei sobre o resultado autorizado da consulta anterior.',
                     {**ficha, 'tipo': 'calculo_resultado', 'tempo_estagio': bool(resultado.periodos)}, resultado=resultado)
    if acao == "historico_cargo":
        from . import historico_funcional
        from .periodo import periodo_da_mensagem

        nome = d.alvo or historico_funcional.nome_contexto(user, c, texto, d)
        if not nome:
            return Saida("De qual funcionário você quer o histórico de cargos e funções? Informe o nome.", ficha)
        tempo = historico_funcional.contexto_estagio(user, c, texto)
        anterior = c.mensagens.filter(papel='ia').order_by('-id').first() if tempo else None
        periodo = periodo_da_mensagem(texto, timezone.localdate())
        filial = next((v for k, v in d.parametros if k == 'filial-analise'), None)
        if anterior and not historico_funcional.pede_tempo_estagio(texto):
            periodo = periodo or anterior.ficha.get('periodo')
            filiais = (anterior.resultado or {}).get('escopo', {}).get('filiais', [])
            preservada = anterior.ficha.get('filial') or (filiais[0] if len(filiais) == 1 else None)
            filial = (filial or preservada) if re.search(r'filia|matriz|todas', texto, re.I) else preservada
        try:
            resultado = historico_funcional.consultar(user, c, nome, periodo,
                interrompido=interrompido, filial=filial, tempo_estagio=tempo,
                dias_uteis=historico_funcional.pede_dias_uteis(texto))
        except PermissaoNegada as exc:
            return Saida(str(exc), ficha, observacao=Observacao(acao, SEM_PERMISSAO, "histórico não autorizado"))
        except ValidationError as exc:
            return Saida(" ".join(exc.messages), ficha)
        except prazo.PrazoEsgotado:
            return Saida("O prazo terminou antes de concluir o histórico. A busca de matrículas não foi tratada como resposta final.", ficha)
        if tempo and not resultado.periodos:
            return Saida('Consultei as fontes autorizadas, mas elas não comprovam um intervalo de estágio para calcular. '
                         'A tabela mostra o histórico disponível; falta uma data ou classificação comprovada do estágio.',
                         {**ficha, 'tipo': 'impedimento_dados', 'busca': nome}, resultado=resultado)
        if tempo and resultado.periodos and not historico_funcional.pede_dias_uteis(texto):
            from .objetivos import metrica_exigida
            resultado.medida_calculada = metrica_exigida(texto) or resultado.medida_calculada
        return Saida("Consultei os vínculos, o histórico funcional e as descrições disponíveis.",
                     {**ficha, "tipo": "historico_cargo", "busca": nome, 'tempo_estagio': tempo,
                      'periodo': periodo, 'filial': filial}, resultado=resultado)
    if acao in ("ajustar_filtro", "voltar_filtro"):
        from .aprendizado import TODOS, alcance_padrao

        alcance = (d.ajuste or {}).get("alcance") or alcance_padrao(user)
        if alcance == TODOS and not PEDE_AJUSTE_RE.search(texto or ""):
            # M41: o filtro fixo é de todos; mudar sem a pessoa pedir altera a resposta de todos.
            # M42: o filtro só da pessoa não passa por esta trava (ela pede do jeito que quiser).
            msg = ("Ajustar ou voltar filtro só quando a pessoa pede. Ela não pediu: não mude a consulta "
                   "compartilhada. Use outra consulta que já responda (veja os objetivos) ou crie uma nova "
                   "(aprender_tabela / criar_consulta) com os filtros que esta pergunta precisa.")
            return Saida("", ficha, observacao=Observacao(acao, ERRO_PARAMETROS, msg))  # aviso só para o modelo
        return _ajustar_filtro(user, d, ficha, alcance, objetivos)
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

        from .objetivos import ano_admissoes_e_desligamentos

        ano = ano_admissoes_e_desligamentos(texto)
        lido = f"01/01/{ano}..31/12/{ano}" if ano else periodo_da_mensagem(texto, timezone.localdate())
        if lido:
            pares.append(("periodo-analise", lido))
        elif getattr(OBJETIVOS[objetivo], "modelo_id", None):
            # M132: consulta aprendida e a pessoa não citou período ("pedidos em aberto", "ativos hoje"): a situação
            # atual é todo o histórico até hoje (como em entregas). A IA entrega em vez de parar perguntando.
            pares.append(("periodo-analise", f"01/01/1900..{timezone.localdate():%d/%m/%Y}"))
    erros = orq._aplicar_parametros(user, atual, pares, defs)
    atual.refresh_from_db()
    return _avaliar_e_executar(user, atual, d.resposta, erros, ficha)


def _frase_pendencias(r) -> str:
    partes = []
    for p in r.perguntas:
        extra = ""
        if p.opcoes:
            extra = " (" + ", ".join(p.opcoes[:12]) + ")"
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
            ficha["opcoes"] = r.perguntas[0].opcoes[:12]  # M96: 8 filiais + "Todas as filiais" (antes cortava em 6)
        # M132: o texto do modelo ("vou montar…") não pode esconder o que falta: sem pergunta, vai a frase do sistema.
        texto = (resposta_modelo if resposta_modelo and resposta_modelo.rstrip().endswith("?")
                 else _frase_pendencias(r) or resposta_modelo)
        # Falta uma decisão da pessoa: pergunta. Só há o que corrigir sozinha se um valor foi recusado.
        obs = _obs_erros(texto_erros) if texto_erros else None
        return Saida(" ".join(x for x in (texto_erros, texto) if x), ficha, s, observacao=obs)
    if r.tipo != "pronta":
        situacao = ERRO_METADADOS if r.mensagem == orq.MOTIVOS["metadados_indisponiveis"] else SEM_PERMISSAO
        if r.mensagem == orq.MOTIVOS["fonte_ausente"]:
            obs = Observacao("preparar_consulta", ERRO_METADADOS, r.mensagem)
        else:
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
    if res.ok and getattr(res, "legenda_pendente", ""):
        idx = next((i for i, c in enumerate(res.colunas) if c != "Filial"), 0)
        codigos = list(dict.fromkeys(str(l[idx]).strip() for l in res.linhas if str(l[idx]).strip()))[:12]
        return Saida("Entreguei a contagem separada por código" + (f" ({', '.join(codigos)})" if codigos else "")
                     + f". O Protheus não grava o nome de cada código no banco (monta pela rotina {res.legenda_pendente}). Me diga a legenda, o que cada código significa "
                     "(ex.: 1 = aprovado, 2 = cancelado) e eu guardo para as próximas respostas.",
                     {**ficha, "tipo": "legenda_pendente"}, s, res)
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
    # M137: o motivo de cada tentativa fica no log (só nomes de tabelas e mensagens do sistema, nunca dados do ERP),
    # para o T.I saber onde a criação da consulta parou.
    import logging
    logging.getLogger(__name__).warning("aprender_tabela sem consulta: alvo=%r motivo=%s",
                                        (d.alvo or "")[:80], obs.detalhe[:400])
    if not pode_aprender_sozinho(user) and not pode_aprender(user):
        return Saida("Procurei no dicionário do Protheus e não consegui montar essa consulta ("
                     + obs.detalhe + "). Diga outras palavras para o assunto ou o código da tabela (ex.: SRA).",
                     ficha, observacao=obs)
    try:
        lista = candidatos(user, d.alvo or texto, automatico=pode_aprender_sozinho(user))
    except Exception:
        return Saida("Não consegui ler o dicionário do Protheus agora. Tente de novo em instantes.", ficha,
                     observacao=Observacao(d.acao, TEMPO_ESGOTADO, "dicionário do ERP indisponível"))
    if not lista:
        return Saida("Não achei tabelas relacionadas no dicionário. Pode me dizer outras palavras ou o código da "
                     "tabela (ex.: SRA)?", ficha, observacao=obs)
    if pode_aprender_sozinho(user):
        # M99: quem a IA pode ensinar sozinha nunca recebe "escolha uma tabela": a IA recebe o motivo e as tabelas
        # candidatas e tenta de novo (outra tabela, outros campos ou o campo mais provável da tabela em uso).
        obs = Observacao(d.acao, ERRO_METADADOS, (obs.detalhe + ". Candidatas: "
                                                  + ", ".join(f"{i['alias']} ({i['nome']})" for i in lista[:5]))[:600])
        return Saida("", ficha, observacao=obs)
    texto = (d.resposta + "\n\n" if d.resposta else "") + "Encontrei estas tabelas no dicionário. Escolha uma para eu aprender:"
    return Saida(texto, {**ficha, "tipo": "aprendizado", "candidatos": lista}, observacao=obs)


def _termos_em_cadastros(user, alias, pedido, maximo=6) -> dict:
    """M154: {campo: {"cadastro", "campo_nome", "contagens"}} dos cadastros de domínio da tabela (SX9) que têm algum
    termo do pedido no nome. Só contagens: o modelo descobre que o termo é uma família/categoria a filtrar, ou que
    nenhum registro tem o termo (falta a definição de negócio), sem ver nenhuma linha do ERP."""
    termos = sorted(aprendizado._radicais(pedido or "") - aprendizado.termos_empresa())[:4]
    filiais = [f for f in _filiais(user) if f != "TODAS"]
    if not termos or not filiais:
        return {}
    from iativa.metadata.catalogo import abrir_catalogo
    from iativa.metadata.descoberta import _Visibilidade
    from iativa.query.nomes import _MAX_CADASTRO, termos_no_cadastro
    out = {}
    try:
        restritas = aprendizado._restritas(user)
        with abrir_catalogo() as cat:
            mapa = {c: t for c, t in aprendizado.cadastros_de_dominio(cat, alias).items() if t not in restritas}
            vis = _Visibilidade(setores_autorizados(user, Permissao.CONSULTAR), sorted(set(mapa.values())))
            sondados = 0
            for campo, t in mapa.items():
                if sondados >= maximo or prazo.esgotado():
                    break
                mestre = cat.tabelas([t], com_campos=True).get(t)
                n = cat.linhas(mestre.fisica) if mestre is not None else None
                if mestre is None or n is None or n == 0 or n > _MAX_CADASTRO:
                    continue
                sondados += 1
                r = termos_no_cadastro(cat, mestre, termos, filiais[0], vis.campo, prazo.limitar(10))
                if r and any(r["contagens"].values()):
                    out[campo] = {"cadastro": f"{t} ({mestre.nome})", **r}
    except Exception as exc:  # auxiliar da descoberta: nunca impede a consulta; nada do ERP no log
        import logging
        logging.getLogger(__name__).warning("termos no cadastro não sondados: %s (%s)", alias, type(exc).__name__)
    return out


def _campos_para_modelo(user, alias, pedido=""):
    info = aprendizado.campos_da_tabela(user, alias, automatico=True)
    por_cadastro = aprendizado.dominios_por_cadastro(user, alias)  # M154: ex.: T9_STATUS → TQY (Status do bem)
    termos_cad = _termos_em_cadastros(user, alias, pedido)  # M154: ex.: T9_CODFAMI → ST6 com "módulo" no nome
    campos = [{"campo": f.nome, "tipo": {"D": "data", "N": "numero"}.get(f.tipo, "texto"), "titulo": f.titulo, "descricao": f.descricao,
               "opcoes": info["opcoes"].get(f.nome, {}),
               **({"origem_dominio_dinamico": info["dinamicos"][f.nome]} if f.nome in info["dinamicos"] else {}),
               **({"dominio_por_cadastro": por_cadastro[f.nome]}
                  if f.nome in por_cadastro and not info["opcoes"].get(f.nome) else {}),
               **({"termos_do_pedido_no_cadastro": termos_cad[f.nome]} if f.nome in termos_cad else {})}
              for f in info["campos"] if not aprendizado.sensivel(f)]
    try:
        juncoes = [{"tabela": j["tabela"], "nome": j["nome"],
                    **({"via": j["via"]} if j.get("via") else {}),
                    "campos": [{"campo": f.nome, "titulo": f.titulo} for f in j["campos"]][:40]}
                   for j in aprendizado.juncoes_possiveis(user, alias, profundidade=2)][:8]
    except Exception:
        juncoes = []
    # M95: poucos trechos técnicos da memória (só desta tabela autorizada; nunca dados do ERP). Campo da referência
    # que não existe na instalação não é oferecido: a estrutura válida é a do dicionário local acima.
    locais = {c["campo"] for c in campos}
    try:
        from iativa.knowledge.memoria_tecnica import recuperar
        ref = recuperar(pedido, aliases=[alias], permitidos={alias}, limite=4)
        referencia = [{"campo": t["campo"], "secao": t["secao"], "texto": t["texto"]} for t in ref.trechos
                      if not t["campo"] or t["campo"] in locais]
        memoria = ref.resumo()
    except Exception:
        referencia, memoria = [], {"estado": "indisponivel"}
    if len(campos) > 150:
        termos = aprendizado._radicais(pedido)
        campos.sort(key=lambda c: (c["campo"] in info["chave"],
                                   len(termos & aprendizado._radicais(c["titulo"] + " " + c["descricao"]))),
                    reverse=True)
    return {"tabela": info["alias"], "nome": info["nome"], "chave_local": info["chave"],
            "natureza": aprendizado.natureza_tabela(info["nome"], info["chave"], info["campos"]),
            "campos": campos[:150], "juncoes_possiveis": juncoes,
            "referencia_publica": referencia, "memoria_tecnica": memoria}


def _fonte_vazia(d, saida) -> str:
    """M104: alias da tabela de uma consulta aprendida que voltou sem linhas porque está VAZIA na instalação."""
    r = saida.resultado
    if (d.acao != "preparar_consulta" or not getattr(r, "ok", False) or getattr(r, "linhas", None)
            or d.objetivo not in OBJETIVOS or not OBJETIVOS[d.objetivo].modelo_id):
        return ""
    alias = OBJETIVOS[d.objetivo].tabelas[0]
    try:
        from iativa.metadata.catalogo import abrir_catalogo
        with abrir_catalogo() as cat:
            t = cat.tabelas([alias]).get(alias)
            return alias if t is not None and cat.vazia(t.fisica) else ""
    except Exception:
        return ""


def _busca_na_descricao(d):
    """M94: decisão alternativa com o texto da busca por nome como "contém" na coluna de descrição da consulta."""
    pares = dict(d.parametros)
    busca = (pares.get(BUSCA) or "").strip()
    if d.acao != "preparar_consulta" or not busca or d.objetivo not in OBJETIVOS or not OBJETIVOS[d.objetivo].modelo_id:
        return None
    cm = ConsultaModelo.objects.filter(pk=OBJETIVOS[d.objetivo].modelo_id).first()
    if cm is None:
        return None
    from .contratos import campo_busca, campos_filtraveis
    nome = campo_busca(cm.spec)
    col = next((c for c, _ in campos_filtraveis(cm.spec) if c != nome and re.search(r"DESC", c)), None)
    if col is None or not re.fullmatch(r"[0-9A-Za-zÀ-ÿ][0-9A-Za-zÀ-ÿ .,/-]{0,59}", busca):
        return None
    filtro = "; ".join(x for x in (pares.get("filtro-campo", ""), f"{col}~{busca}") if x)
    novos = tuple((k, v) for k, v in d.parametros if k not in (BUSCA, "filtro-campo")) + (("filtro-campo", filtro),)
    return replace(d, parametros=novos)


def _lembrar_fonte(user, cm, setor):
    """M82: o que a IA aprendeu sozinha fica na memória (visível em Memória e aprovações): o assunto, a consulta
    e a tabela do Protheus. Só estrutura; nunca nomes, valores ou linhas do ERP."""
    from .models import EscopoNota, NotaAgente, TipoNota
    texto = (f"Para perguntas sobre {cm.descricao or cm.nome}, use a consulta “{cm.nome}” (tabela {cm.tabela} do "
             f"Protheus), aprendida pela IA.")[:300]
    try:
        if not NotaAgente.objects.filter(texto=texto, ativo=True, setor=setor).exists():
            NotaAgente.objects.create(texto=texto, tipo=TipoNota.TERMO, escopo=EscopoNota.SETOR, setor=setor,
                                      setor_origem=setor, setores_origem=[setor.codigo], criado_por=user,
                                      aprovado_por=user, aprovado_em=timezone.now(), evidencia=f"consulta {cm.codigo}"[:300])
    except Exception:
        pass  # a memória é auxiliar: nunca impede a resposta


def tentadas_descoberta(entrada) -> set:
    plan = getattr(entrada, "planejamento", None) or {}
    return set(plan.get("fontes_inconclusivas", [])) | set(plan.get("fontes_vazias", []))


def _outras_fontes(user, alvo, excluir, pedido, limite=2) -> list:
    """M154: o modelo pediu outra fonte com as tabelas em mãos. Só entra tabela nova (não vista, não vazia nem
    inconclusiva); pedir de novo as mesmas tabelas não é outra fonte."""
    citadas = set(aprendizado._aliases_citados(alvo or ""))
    if citadas and citadas <= set(excluir):
        return []
    try:
        lista = candidatos(user, alvo or pedido, limite=limite + len(excluir), automatico=True)
    except Exception:
        return []
    out = []
    for item in lista:
        if item["alias"] in excluir or len(out) >= limite:
            continue
        try:
            out.append(_campos_para_modelo(user, item["alias"], pedido))
        except Exception:
            continue
    return out


def _aprender_sozinho(user, c, d, texto, ficha, provedor, entrada, atual, motivo=None):
    motivo = motivo if motivo is not None else []
    consulta = d.consulta
    if consulta is None:
        try:
            indicadas = entrada.planejamento.get("analise", {}).get("fontes_indicadas", [])
            if indicadas and d.alvo == " ".join(indicadas):
                lista = [{"alias": a} for a in indicadas]
            else:
                tentadas = set(entrada.planejamento.get("fontes_inconclusivas", []))
                tentadas.update(entrada.planejamento.get("fontes_vazias", []))
                lista = candidatos(user, d.alvo or texto, limite=3 + len(tentadas), automatico=True)
                lista = [item for item in lista if item["alias"] not in tentadas][:3]
        except Exception:
            motivo.append("não foi possível ler o dicionário do ERP")
            return None
        tabelas = []
        vazias = set(entrada.planejamento.get("fontes_vazias", []))
        lista = [i for i in lista if i["alias"] not in vazias]  # M104: tabela vazia na instalação não é candidata
        for item in lista:
            if indicadas and d.alvo == " ".join(indicadas):
                from .planejamento import FonteInconclusiva, autorizar_fonte
                try:
                    autorizar_fonte(user, provedor, item["alias"], conversa=c, transversais=set())
                except FonteInconclusiva as exc:
                    motivo.append(str(exc))
                    continue
                except PermissaoNegada as exc:
                    return Saida(str(exc), {**ficha, "tipo": "bloqueada"},
                                 observacao=Observacao(d.acao, SEM_PERMISSAO, str(exc)))
                except ImproperlyConfigured:
                    motivo.append("classificador indisponível")
                    continue
            try:
                tabelas.append(_campos_para_modelo(user, item["alias"], d.alvo or texto))
            except Exception:
                continue  # sem campo filial, restrita etc.
        if not tabelas:
            motivo.append(f"nenhuma tabela utilizável para “{(d.alvo or texto)[:60]}”")
            return None
        andamento.avisar("Montando a consulta nova com os campos de " + ", ".join(t["tabela"] for t in tabelas))
        # M154: com as tabelas em mãos, a decisão fica restrita a montar a consulta, pedir outra fonte (com o
        # motivo) ou perguntar uma definição de negócio. Antes o esquema inteiro valia e o modelo voltava a
        # escolher a consulta reprovada (preparar_consulta) ou pedia as mesmas tabelas de novo.
        entrada2 = replace(entrada, tabelas_para_aprender=tabelas, acoes_permitidas=ETAPA_MONTAR,
                           observacoes=list(getattr(entrada, "observacoes", None) or []) + [{
                               "acao": "aprender_tabela", "situacao": "tabelas_em_maos",
                               "motivo": ETAPA_MONTAR_TEXTO}])
        codigos_par = sorted({p["codigo"] for o in entrada.objetivos for p in o.get("parametros", [])}
                             | set(PARAMETROS_APRENDIDOS))
        codigos_obj = [o["codigo"] for o in entrada.objetivos]
        d2 = _interpretar(provedor, entrada2, codigos_obj, codigos_par)
        for rodada in range(2):
            if d2.acao != "aprender_tabela":
                break
            vistas = {t["tabela"] for t in tabelas}
            novas = [] if rodada else _outras_fontes(user, d2.alvo, vistas | tentadas_descoberta(entrada), d2.alvo or texto)
            if novas:
                tabelas = tabelas + novas
                andamento.avisar("Incluindo outra fonte: " + ", ".join(t["tabela"] for t in novas))
                obs = {"acao": "aprender_tabela", "situacao": "outra_fonte",
                       "motivo": "Acrescentei " + ", ".join(t["tabela"] for t in novas) + " às tabelas. " + ETAPA_MONTAR_TEXTO}
            else:
                # M138: as tabelas pedidas já estão em mãos (ou não há outra fonte com esses termos): montar agora.
                obs = {"acao": "aprender_tabela", "situacao": "etapa_errada",
                       "motivo": ("As tabelas e os campos já estão em tabelas_para_aprender e não há outra fonte para "
                                  "esses termos. Agora responda acao 'criar_consulta' com a tabela que responde a pergunta ("
                                  + ", ".join(t["tabela"] for t in tabelas) + "), as colunas, o campo_data, as juncoes "
                                  "para trazer nomes e a agregacao pedida (ex.: contagem_distinta:CAMPO por CAMPO_GRUPO, "
                                  "com top N se pedido), ou faça a pergunta de negócio. Não peça a tabela de novo.")}
            entrada2 = replace(entrada2, tabelas_para_aprender=tabelas,
                               observacoes=list(entrada2.observacoes or []) + [obs])
            d2 = _interpretar(provedor, entrada2, codigos_obj, codigos_par)
        if d2.acao == "conversar" and d2.resposta.strip().endswith("?"):
            return Saida(d2.resposta, {**ficha, "tipo": "esclarecimento", "opcoes": list(d2.opcoes),
                                       "pedido_original": texto[:1000]})
        if d2.acao != "criar_consulta" or d2.consulta is None:
            motivo.append("as tabelas encontradas (" + ", ".join(t["tabela"] for t in tabelas) + ") não serviram"
                          + f" (o modelo respondeu {d2.acao or 'nada'})")
            return None
        d, consulta = d2, d2.consulta
    consulta = {**consulta, "campo_data": consulta.get("campo_data", "").upper(),
                "campo_busca": consulta.get("campo_busca", "").upper()}
    andamento.avisar(f"Criando a consulta “{consulta.get('nome', '')}” ({consulta.get('tabela', '')})")
    try:
        from .planejamento import FonteInconclusiva, autorizar_fonte
        setores = None
        transversais = set()
        pedidas_nomes = [j["tabela"] for j in consulta.get("juncoes", [])]
        vias = []
        if pedidas_nomes:
            try:
                mapa_j = {j["tabela"]: j for j in aprendizado.juncoes_possiveis(user, consulta["tabela"], profundidade=2)}
                vias = [mapa_j[t]["via"] for t in pedidas_nomes if t in mapa_j and mapa_j[t].get("via")]
            except Exception:
                vias = []
        for alias in dict.fromkeys([consulta["tabela"], *vias, *pedidas_nomes]):
            autorizados = autorizar_fonte(user, provedor, alias, conversa=c, transversais=transversais)
            setores = autorizados if setores is None else setores & autorizados
        cm = aprendizado.criar_automatico(user, consulta, setores_confirmados=setores,
                                         leitura_transversal=bool(transversais))
    except PermissaoNegada as exc:
        return Saida(str(exc), {**ficha, 'tipo': 'bloqueada'},
                     observacao=Observacao(d.acao, SEM_PERMISSAO, str(exc)))
    except (FonteInconclusiva, ImproperlyConfigured) as exc:
        motivo.append(str(exc) if isinstance(exc, FonteInconclusiva) else 'classificador indisponível')
        return None
    except ValidationError as exc:
        registrar("modelo_falhou", "agente", usuario=user, estado_novo="aprendizado_automatico", resultado="erro")
        detalhe = "; ".join(getattr(exc, "messages", None) or [str(exc)])
        motivo.append(f"consulta sobre {consulta.get('tabela', '')} recusada: {detalhe}"[:300])
        return None
    if cm.situacao != SituacaoModelo.APROVADA:
        # M81: fonte nova proposta por quem não é da TI: espera a autorização, não consulta antes.
        return Saida(f"Para responder preciso de uma fonte nova: “{cm.nome}” ({cm.tabela}). Deixei a proposta para "
                     "a TI autorizar; assim que for autorizada, é só perguntar de novo.",
                     {**ficha, "tipo": "bloqueada", "fonte_proposta": cm.pk})
    registrar("interpretacao", "agente", usuario=user, estado_novo="aprendeu_sozinho", entidade_id=cm.pk)
    codigos = set(OBJETIVOS[cm.codigo].parametros)
    defs = orq._definicoes(sorted(codigos))
    d3 = replace(d, acao="preparar_consulta", objetivo=cm.codigo,
                 parametros=tuple((k, v) for k, v in d.parametros if k in codigos),
                 resposta=(f"Aprendi a consultar “{cm.nome}” ({cm.tabela}). " + (d.resposta or "")).strip())
    saida = _preparar(user, c, d3, atual, defs, ficha, texto)
    saida.decisao_efetiva = d3
    if (getattr(saida.resultado, "ok", False) and not cm.spec.get("leitura_transversal")
            and not getattr(saida.resultado, "legenda_pendente", "")):
        _lembrar_fonte(user, cm, saida.solicitacao.setor)
    return saida


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


def _ajustar_filtro(user, d, ficha, alcance="", objetivos=()) -> Saida:
    """M40/M42: muda (ou desfaz) os filtros de uma consulta aprendida a pedido da pessoa: os fixos (T.I,
    valem para todos) ou os só dela."""
    from .aprendizado import USUARIO, ajustar_filtros, descrever_filtros, voltar_filtros

    achados = _achar_modelo(d.alvo)
    if not achados:
        nomes = "; ".join(f"“{m.nome}”" for m in ConsultaModelo.objects.filter(situacao=SituacaoModelo.APROVADA))
        return Saida("Qual consulta devo ajustar?" + (f" As que existem são: {nomes}." if nomes else ""), ficha)
    if len(achados) > 1:
        ficha["opcoes"] = [m.nome for m in achados[:6]]
        return Saida("Encontrei mais de uma. Qual delas?", ficha)
    cm = achados[0]
    so_seu = alcance == USUARIO
    if so_seu and cm.codigo not in {o.codigo for o in objetivos}:
        return Saida("Você não tem acesso a essa consulta.", ficha,
                     observacao=Observacao(d.acao, SEM_PERMISSAO, "consulta fora do acesso"))
    quem = user if so_seu else None
    antes = descrever_filtros(cm, quem)
    try:
        if d.acao == "voltar_filtro":
            cm = voltar_filtros(user, cm, alcance)
        else:
            cm = ajustar_filtros(user, cm, d.ajuste["filtros"], d.ajuste["remover"], alcance)
            if getattr(cm, 'excecao_solicitada', None):
                return Saida(f'Isso altera um filtro corporativo. Enviei o pedido para aprovação da Diretoria ou TI '
                             f'(nota-{cm.excecao_solicitada}). O filtro atual continua valendo.',
                             {**ficha, 'aprovacao_pendente': cm.excecao_solicitada})
    except PermissaoNegada as exc:
        return Saida(str(exc), ficha, observacao=Observacao(d.acao, SEM_PERMISSAO, "ajuste não autorizado"))
    except (ValidationError, ValueError) as exc:
        motivo = " ".join(getattr(exc, "messages", [str(exc)]))
        return Saida(motivo, ficha, observacao=Observacao(d.acao, ERRO_METADADOS, motivo))
    if so_seu:
        verbo = "Voltei os seus filtros" if d.acao == "voltar_filtro" else "Ajustei só para você"
        return Saida(f"{verbo} na consulta “{cm.nome}”. Antes: {antes}. Agora: {descrever_filtros(cm, user)}. "
                     "Os outros usuários continuam com os filtros fixos. Para desfazer, peça para voltar o filtro.",
                     ficha)
    verbo = "Voltei os filtros" if d.acao == "voltar_filtro" else "Ajustei os filtros"
    return Saida(f"{verbo} da consulta “{cm.nome}” (valem para todos). Antes: {antes}. Agora: {descrever_filtros(cm)}. "
                 "Para desfazer, peça para voltar o filtro como era antes.", ficha)


def _lembrar_parametros(user, d) -> list[str]:
    """M55: "para mim, atraso só depois de 5 dias" é o valor de um parâmetro (tolerância), não uma regra de
    campo: grava como preferência do usuário, que a resolução já aplica nas próximas consultas."""
    from iativa.memory.models import Escopo, ParameterDefinition
    from iativa.memory import servicos as memoria_valores

    from .conversao import converter
    from .orquestrador import _exibir

    feitas = []
    for codigo, texto in d.parametros:
        definicao = ParameterDefinition.objects.filter(codigo=codigo, ativo=True).first()
        if definicao is None or definicao.contem_dado_pessoal:
            continue
        try:
            valor = converter(definicao, texto)
            binding = memoria_valores.registrar_valor(user, definicao, Escopo.USUARIO, valor)
        except Exception:
            continue
        if binding.excecao_de_id:
            feitas.append(f'{definicao.nome}: exceção pendente de aprovação da Diretoria ou TI (valor-{binding.pk}); a regra corporativa continua valendo')
        else:
            feitas.append(f"{definicao.nome} = {_exibir(definicao, valor)}")
    return feitas


def _lembrar(user, d, ficha) -> Saida:
    andamento.avisar("Guardando na memória")
    preferencias = _lembrar_parametros(user, d)
    if preferencias:
        if any('pendente de aprovação' in p for p in preferencias):
            return Saida('; '.join(preferencias) + '.', {**ficha, 'memoria_registrada': True})
        return Saida("Guardei como sua preferência: " + "; ".join(preferencias)
                     + ". Vale nas suas próximas consultas, até você pedir outra coisa.",
                     {**ficha, "memoria_registrada": True})
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
        return Saida(texto, ficha, observacao=Observacao("lembrar", ERRO_PARAMETROS, "nota recusada"))
    except Exception:  # PermissaoNegada
        return Saida("Não consegui guardar isso para o setor: seu usuário está sem setor ativo.", ficha,
                     observacao=Observacao("lembrar", SEM_PERMISSAO, "memória não registrada"))
    ficha["memoria_registrada"] = True
    if n.excecao_de_id or n.excecao_consulta_id:
        return Saida(f'Pedido registrado para aprovação da Diretoria ou TI (nota-{n.pk}). '
                     'A regra corporativa continua valendo; a exceção pessoal ainda não foi aplicada.', ficha)
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
