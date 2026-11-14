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
                        "fonte", "exigencia", "causa", "tabela"}
# M38: trilha do pedido que parou sem evidência ("ação:situação:evidência"), só códigos.
TRILHA_ITEM = re.compile(r"^[a-z_-]{1,40}:[a-z_-]{1,40}:[a-z_-]{1,40}$")
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


_EMAIL = re.compile(r"\S+@\S+")
_DATA = re.compile(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b")
_NUMERO_LONGO = re.compile(r"\d[\d.\-/]{3,}\d")
_NOME = re.compile(r"(?<=[^\s.!?:])(\s+)[A-ZÀ-Ý][a-zà-ÿ]+(?:\s+(?:d[aeo]s?\s+)?[A-ZÀ-Ý][a-zà-ÿ]+)*")
PERGUNTA_PERMITIDA = re.compile(r"^[\w\s.,;:!?()\[\]/%+\-–'\"ºª°]{1,300}$")


def mascarar_pergunta(texto: str) -> str:
    """M51: a pergunta vai à manutenção para ela saber QUAL capacidade falta, nunca com dado pessoal:
    e-mails, números longos (CPF, CNPJ, matrícula, valores) e nomes próprios viram marcadores.
    Datas curtas ficam (definem o período). Nada que venha do ERP passa por aqui."""
    t = " ".join(str(texto or "").split())[:300]
    datas = []
    t = _DATA.sub(lambda m: datas.append(m.group(0)) or f"\x00{len(datas) - 1}\x00", t)
    t = _EMAIL.sub("[email]", t)
    t = _NUMERO_LONGO.sub("[numero]", t)
    t = re.sub(r"\d+[.,]\d+", "[numero]", t)
    t = _NOME.sub(lambda m: m.group(1) + "[nome]", t)
    t = re.sub(r"\x00(\d+)\x00", lambda m: datas[int(m.group(1))], t)
    t = "".join(ch for ch in t if ch.isprintable())
    return t if PERGUNTA_PERMITIDA.match(t) else ""


def _condicoes(cond: dict | None) -> dict:
    out = {}
    trilha = (cond or {}).get("trilha")
    if isinstance(trilha, (list, tuple)):
        limpa = [x for x in trilha if isinstance(x, str) and TRILHA_ITEM.match(x)][-30:]
        if limpa:
            out["trilha"] = limpa
    pergunta = mascarar_pergunta((cond or {}).get("pergunta", ""))
    if pergunta:
        out["pergunta"] = pergunta
    for k, v in (cond or {}).items():
        if k in ("trilha", "pergunta"):
            continue
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


def acordar_manutencao() -> None:
    """M50: avisa o executor do host na hora (arquivo no volume vigiado por iativa-manutencao.path), em vez
    de esperar o agendamento. Só um carimbo de hora: nenhum dado do incidente sai da aplicação."""
    from pathlib import Path

    from django.conf import settings

    try:
        arq = Path(settings.BASE_DIR) / "var" / "manutencao-acordar"
        arq.parent.mkdir(parents=True, exist_ok=True)
        arq.write_text(timezone.now().isoformat())
    except Exception as erro:
        logger.warning("manutenção não acordada: %s", type(erro).__name__)


def registrar(componente: str, operacao: str, exc=None, categoria: str = "", condicoes: dict | None = None):
    inc = _registrar(componente, operacao, exc, categoria, condicoes)
    if inc is not None and elegivel(inc):
        acordar_manutencao()
    return inc


def _registrar(componente: str, operacao: str, exc=None, categoria: str = "", condicoes: dict | None = None):
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
        limpas = _condicoes(condicoes)
        # Erro técnico com rastreamento, ou capacidade ausente com a trilha do pedido (triagem: a manutenção
        # só corrige se reproduzir com dados sintéticos; ver scripts/manutencao.py).
        elegivel = (categoria == Categoria.ERRO_TECNICO and bool(rastro)) or (
            categoria == Categoria.CAPACIDADE and bool(limpas.get("trilha")))
        with transaction.atomic():
            atual = Incidente.objects.select_for_update().filter(assinatura=assinatura).first()
            if atual is None:
                try:
                    with transaction.atomic():
                        return Incidente.objects.create(
                            assinatura=assinatura, categoria=categoria, codigo=codigo, componente=componente[:60],
                            operacao=operacao[:80], versao=versao(), correlacao=correlacao, rastreamento=rastro,
                            condicoes=limpas,
                            situacao=SituacaoIncidente.ABERTO if elegivel else SituacaoIncidente.NAO_ELEGIVEL)
                except IntegrityError:
                    atual = Incidente.objects.select_for_update().get(assinatura=assinatura)
            mudancas = {"ocorrencias": F("ocorrencias") + 1, "correlacao": correlacao, "versao": versao(),
                        "ultima_em": timezone.now()}
            if limpas.get("pergunta"):  # M51: a manutenção trabalha sobre o caso mais recente
                mudancas["condicoes"] = {**(atual.condicoes or {}), **limpas}
            if atual.situacao == SituacaoIncidente.PUBLICADO:
                # Voltou a acontecer depois da correção publicada (na versão corrigida ou numa posterior):
                # a correção não resolveu este caso. M50: antes só reabria na mesma versão, e o pedido ficava
                # parado esperando uma correção que já tinha acontecido.
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
    if inc.situacao != SituacaoIncidente.ABERTO or inc.tentativas >= MAX_TENTATIVAS:
        return False
    if inc.categoria == Categoria.ERRO_TECNICO:
        return bool(inc.rastreamento)
    return inc.categoria == Categoria.CAPACIDADE and bool((inc.condicoes or {}).get("trilha"))


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
