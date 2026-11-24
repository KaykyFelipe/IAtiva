"""M149: registro de cada resposta da IA que terminou em erro.

Guarda a pergunta da pessoa, a resposta da IA e o que precisa ser corrigido:
- tipo do erro (ficha da resposta ou padrão do texto);
- trilha e motivo do pedido acompanhado (`Objetivo`), que são só códigos;
- o que o modelo decidiu em cada passo (ação, consulta, texto curto da resposta dele) e as observações que o sistema
  devolveu a ele. O modelo nunca vê linhas do ERP, então nada disso traz dados do ERP.

O registro nasce de um único ponto (gravação da resposta da IA), então vale para chat, continuação, retomada e
qualquer outro caminho. Falha do registro nunca derruba a resposta.
"""
import logging
import os
import re
from contextvars import ContextVar

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import AvaliacaoResposta, ErroResposta, Mensagem, Papel

log = logging.getLogger(__name__)

_passos: ContextVar = ContextVar("iativa_passos_do_pedido", default=None)
MAX_PASSOS = 12

FALHA_RE = re.compile(
    r"^(?:(?:Continuando o seu pedido, sem você precisar pedir de novo|Depois da correção publicada, refiz o seu "
    r"pedido)\. )?(Não terminei esta resposta|Sem permissão de consulta|A fonte não é autorizada|Esta receita não está "
    r"liberada|Essa fonte pertence a outro setor|O ERP ou a IA não responderam|Não consegui|A consulta depende de "
    r"acesso vigente|Permissão insuficiente|A IA \(Gemini\) está sobrecarregada|A IA demorou)")
FICHAS_ERRO = {"bloqueada", "falha_execucao", "falha_modelo", "fora_escopo", "impedimento_dados"}


def anotar_passo(decisao, observacoes=()) -> None:
    """Chamado pelo agente a cada decisão do modelo. Só códigos e textos do próprio modelo/sistema."""
    passos = _passos.get()
    if passos is None:
        passos = []
        _passos.set(passos)
    if len(passos) >= MAX_PASSOS:
        return
    passos.append({
        "acao": getattr(decisao, "acao", ""),
        "objetivo": getattr(decisao, "objetivo", "") or "",
        "resposta": (getattr(decisao, "resposta", "") or "")[:300],
        "observacoes": [f"{getattr(o, 'acao', '')}:{getattr(o, 'situacao', '')}: {str(getattr(o, 'detalhe', ''))[:200]}"
                        for o in list(observacoes)[-3:]],
    })


def _consumir_passos() -> list:
    passos = list(_passos.get() or [])
    _passos.set(None)
    return passos


def tipo_erro(m) -> str:
    """Tipo do erro desta resposta da IA, ou "" se não for erro."""
    if m.papel != Papel.IA:
        return ""
    ficha = m.ficha if isinstance(m.ficha, dict) else {}
    if ficha.get("dado_protegido"):
        return ""  # recusa de dado pessoal protegido é regra, não erro
    resultado = m.resultado if isinstance(m.resultado, dict) else {}
    if resultado and resultado.get("ok", True) and ficha.get("tipo") not in FICHAS_ERRO:
        return ""
    if ficha.get("tipo") in FICHAS_ERRO:
        return ficha["tipo"]
    if FALHA_RE.match((m.texto or "").strip()):
        return "nao_concluida" if "Não terminei" in (m.texto or "") else "recusa"
    return ""


def _pergunta(m):
    return (Mensagem.objects.filter(conversa_id=m.conversa_id, papel=Papel.USUARIO, pk__lt=m.pk)
            .order_by("-pk").first())


def _descrever(tipo: str, objetivo, passos: list) -> str:
    partes = [f"Tipo: {tipo}."]
    if objetivo is not None:
        partes.append(f"Pedido: exigência {objetivo.exigencia}, situação {objetivo.situacao}"
                      + (f", motivo “{objetivo.motivo}”" if objetivo.motivo else "") + ".")
        if objetivo.trilha:
            partes.append("Trilha: " + " → ".join(objetivo.trilha[-12:]) + ".")
        entregas = (objetivo.contrato or {}).get("entregas") or []
        if entregas:
            partes.append("Entregas pedidas: " + "; ".join(e.get("descricao", "") for e in entregas[:6]) + ".")
    if passos:
        acoes = [p["acao"] + (f"({p['objetivo']})" if p["objetivo"] else "") for p in passos]
        partes.append("Decisões do modelo: " + ", ".join(acoes) + ".")
        ultima = next((p["resposta"] for p in reversed(passos) if p["resposta"]), "")
        if ultima:
            partes.append(f"Última resposta do modelo: “{ultima}”.")
    return " ".join(partes)[:4000]


def registrar(m, passos=None) -> ErroResposta | None:
    """Grava o erro da resposta `m` (Mensagem da IA), se for erro. Nunca levanta exceção."""
    try:
        m.refresh_from_db()
        tipo = tipo_erro(m)
        if not tipo or ErroResposta.objects.filter(mensagem_ia=m).exists():
            return None
        p = _pergunta(m)
        objetivo = None
        if p is not None:
            from iativa.agent.models import Objetivo
            objetivo = Objetivo.objects.filter(mensagem=p).order_by("-pk").first()
        passos = list(passos or [])
        detalhe = {"ficha": (m.ficha or {}).get("tipo", ""), "passos": passos}
        if objetivo is not None:
            detalhe.update({"objetivo": objetivo.pk, "trilha": objetivo.trilha[-20:], "motivo": objetivo.motivo,
                            "situacao": objetivo.situacao})
        return ErroResposta.objects.create(
            conversa_id=m.conversa_id, usuario=getattr(p, "autor", None) or m.conversa.usuario,
            mensagem_usuario=p, mensagem_ia=m, pergunta=(p.texto if p else "")[:2000], resposta=(m.texto or "")[:4000],
            tipo=tipo, erro=_descrever(tipo, objetivo, passos), detalhe=detalhe,
            versao=os.environ.get("IATIVA_VERSAO", "dev"))
    except Exception as exc:  # o registro nunca derruba a resposta; nada do pedido vai para o log
        log.warning("erro da IA não registrado (%s)", type(exc).__name__)
        return None


def registrar_falha(user, conversa, pergunta: str, exc: BaseException) -> ErroResposta | None:
    """Falha inesperada antes de existir resposta (ex.: exceção no atendimento). Guarda só o tipo da exceção e onde
    ela aconteceu no código, nunca a mensagem (pode conter dados)."""
    try:
        import traceback
        local = [f"{f.filename.rsplit('iativa', 1)[-1]}:{f.lineno}:{f.name}"
                 for f in traceback.extract_tb(exc.__traceback__) if "iativa" in f.filename][-6:]
        passos = _consumir_passos()
        return ErroResposta.objects.create(
            conversa=conversa, usuario=user, pergunta=(pergunta or "")[:2000],
            resposta="(sem resposta: o atendimento falhou)", tipo="excecao",
            erro=(f"Exceção {type(exc).__name__} no atendimento. Onde: " + " ← ".join(reversed(local)) + "."
                  + (" " + _descrever("excecao", None, passos) if passos else ""))[:4000],
            detalhe={"excecao": type(exc).__name__, "rastreamento": local, "passos": passos},
            versao=os.environ.get("IATIVA_VERSAO", "dev"))
    except Exception as erro:
        log.warning("falha do atendimento não registrada (%s)", type(erro).__name__)
        return None


@receiver(post_save, sender=Mensagem)
def _ao_salvar_resposta(sender, instance, created, **kwargs):
    if not created or instance.papel != Papel.IA:
        return
    passos = _consumir_passos()
    # O resultado é gravado logo depois da mensagem (`resultados.guardar`): classifica depois do commit.
    transaction.on_commit(lambda: registrar(instance, passos))


@receiver(post_save, sender=AvaliacaoResposta)
def _ao_avaliar_incorreta(sender, instance, **kwargs):
    """A pessoa marcou "Não" em "Esta informação está correta?": também é erro da IA, com o comentário dela."""
    if instance.correta:
        return
    try:
        m = instance.mensagem
        if ErroResposta.objects.filter(mensagem_ia=m, tipo="avaliada_incorreta").exists():
            return
        p = _pergunta(m)
        ErroResposta.objects.create(
            conversa_id=m.conversa_id, usuario=instance.usuario, mensagem_usuario=p, mensagem_ia=m,
            pergunta=(p.texto if p else "")[:2000], resposta=(m.texto or "")[:4000], tipo="avaliada_incorreta",
            erro=("A pessoa marcou a resposta como incorreta. Comentário: " + (instance.comentario or "(sem comentário)"))[:4000],
            detalhe={"avaliacao": instance.pk}, versao=os.environ.get("IATIVA_VERSAO", "dev"))
    except Exception as exc:
        log.warning("avaliação incorreta não registrada como erro (%s)", type(exc).__name__)
