"""Registro e classificação de incidentes (M32).

O agente de atendimento só chama `registrar(...)`: ele não tem Git, Docker nem escrita no código.
Quem corrige é o executor de manutenção (scripts/manutencao.py), fora da aplicação.

Sanitização: o rastreamento guarda só arquivo:linha:função dos arquivos do próprio IAtiva; a
mensagem da exceção (que pode trazer dados do ERP, SQL ou texto do usuário) nunca é guardada.
As condições aceitam só chaves da lista CONDICOES_PERMITIDAS, com valores curtos e simples.
"""
import hashlib
import logging
import os
import re
import traceback
import uuid

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from .models import Categoria, Incidente, SituacaoIncidente

logger = logging.getLogger(__name__)

CONDICOES_PERMITIDAS = {"etapa", "tipo_tarefa", "situacao", "acao", "objetivo_tipo", "metodo", "rota", "provedor",
                        "fonte"}
VALOR_SEGURO = re.compile(r"^[a-z0-9_./:-]{1,60}$")
MAX_TENTATIVAS = 2
MAX_REABERTURAS = 1


def versao() -> str:
    return os.environ.get("IATIVA_VERSAO", "dev")


def _rastreamento(exc) -> list[str]:
    if exc is None or exc.__traceback__ is None:
        return []
    out = []
    for f in traceback.extract_tb(exc.__traceback__):
        caminho = f.filename.replace("\\", "/")
        if "/iativa/" not in caminho or "/site-packages/" in caminho:
            continue
        rel = "iativa/" + caminho.split("/iativa/", 1)[1]
        out.append(f"{rel}:{f.lineno}:{f.name}")
    return out[-12:]


def _condicoes(cond: dict | None) -> dict:
    out = {}
    for k, v in (cond or {}).items():
        v = str(v).strip().lower()
        if k in CONDICOES_PERMITIDAS and VALOR_SEGURO.match(v):
            out[k] = v
    return out


def classificar(exc=None, situacao: str = "") -> str:
    """Categoria pela origem da falha. Só erro técnico do nosso código vira correção automática."""
    from iativa.query.executor import tipo_falha

    if situacao in ("sem_permissao",):
        return Categoria.PERMISSAO
    if situacao in ("tempo_esgotado", "falha_transitoria") or (exc is not None and tipo_falha(exc)):
        return Categoria.TRANSITORIA
    if situacao in ("erro_metadados",):
        return Categoria.FONTE
    if situacao in ("regra_desconhecida", "aguardando_usuario"):
        return Categoria.REGRA_NEGOCIO
    if situacao in ("capacidade_ausente", "verificacao_nao_feita"):
        return Categoria.CAPACIDADE
    return Categoria.ERRO_TECNICO


def registrar(componente: str, operacao: str, exc=None, categoria: str = "", condicoes: dict | None = None):
    """Agrupa pela causa (categoria, componente, operação, tipo da exceção, onde ocorreu). Nunca levanta."""
    try:
        categoria = categoria or classificar(exc)
        rastro = _rastreamento(exc)
        tipo = type(exc).__name__ if exc is not None else "-"
        base = "|".join([categoria, componente, operacao, tipo] + [r.rsplit(":", 2)[0] + ":" + r.rsplit(":", 1)[1]
                                                                    for r in rastro[-3:]])
        assinatura = hashlib.sha256(base.encode()).hexdigest()
        codigo = f"{componente}.{operacao}.{tipo}"[:80]
        correlacao = uuid.uuid4().hex
        elegivel = categoria == Categoria.ERRO_TECNICO and bool(rastro)
        with transaction.atomic():
            atual = Incidente.objects.select_for_update().filter(assinatura=assinatura).first()
            if atual is None:
                try:
                    with transaction.atomic():
                        return Incidente.objects.create(
                            assinatura=assinatura, categoria=categoria, codigo=codigo, componente=componente[:60],
                            operacao=operacao[:80], versao=versao(), correlacao=correlacao, rastreamento=rastro,
                            condicoes=_condicoes(condicoes),
                            situacao=SituacaoIncidente.ABERTO if elegivel else SituacaoIncidente.NAO_ELEGIVEL)
                except IntegrityError:
                    atual = Incidente.objects.select_for_update().get(assinatura=assinatura)
            mudancas = {"ocorrencias": F("ocorrencias") + 1, "correlacao": correlacao, "versao": versao(),
                        "ultima_em": timezone.now()}
            if atual.situacao == SituacaoIncidente.PUBLICADO and atual.versao_corrigida == versao():
                # Voltou a acontecer NA versão corrigida: a correção não resolveu.
                if atual.reaberturas >= MAX_REABERTURAS:
                    mudancas.update(situacao=SituacaoIncidente.BLOQUEADO,
                                    motivo="Voltou a ocorrer depois da correção; precisa de análise humana.")
                else:
                    mudancas.update(situacao=SituacaoIncidente.ABERTO, reaberturas=F("reaberturas") + 1)
            Incidente.objects.filter(pk=atual.pk).update(**mudancas)
            atual.refresh_from_db()
            return atual
    except Exception as erro:  # registrar incidente nunca derruba o atendimento
        logger.warning("incidente não registrado: %s", type(erro).__name__)
        return None


def elegivel(inc: Incidente) -> bool:
    return (inc.categoria == Categoria.ERRO_TECNICO and inc.situacao == SituacaoIncidente.ABERTO
            and inc.tentativas < MAX_TENTATIVAS and bool(inc.rastreamento))


def diagnostico(inc: Incidente) -> dict:
    """O que vai para o ambiente de manutenção: só estrutura sanitizada."""
    return {"incidente": inc.pk, "codigo": inc.codigo, "categoria": inc.categoria, "componente": inc.componente,
            "operacao": inc.operacao, "versao": inc.versao, "correlacao": inc.correlacao,
            "rastreamento": list(inc.rastreamento), "condicoes": dict(inc.condicoes), "ocorrencias": inc.ocorrencias}


def ao_erro_de_requisicao(sender, request=None, **kwargs):
    import sys

    exc = sys.exc_info()[1]
    rota = getattr(getattr(request, "resolver_match", None), "url_name", "") or "desconhecida"
    registrar("web", rota, exc, condicoes={"metodo": getattr(request, "method", "").lower(), "rota": rota})
