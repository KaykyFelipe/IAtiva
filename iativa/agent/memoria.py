"""Memória setorial e fila de aprovação (Etapa 6).

- Notas do agente: usuário (vale na hora), setor e empresa (proposta → aprovação).
- Fila única de pendências: valores de parâmetro, notas e consultas aprendidas.
- Conflito: proposta com o mesmo termo de uma nota aprovada no mesmo nível passa a substituí-la;
  até a decisão, vale a aprovada.

Quem aprova precisa de "aprovar definições" no nível (setor da proposta ou empresa). Quem propôs
não aprova a própria proposta, salvo com a autoaprovação ligada (mesma chave da M9).
"""
import re
import unicodedata
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from iativa.accounts.authz import (
    Permissao,
    PermissaoNegada,
    setor_do_usuario,
    setores_autorizados,
    tem_permissao,
)
from iativa.accounts.models import Department
from iativa.audit.servicos import registrar
from iativa.memory import servicos as memoria_valores
from iativa.memory.models import Escopo, ParameterBinding, Situacao
from iativa.memory.validadores import contem_cpf_cnpj

from . import aprendizado
from .conversao import exibir
from .models import ConsultaModelo, EscopoNota, NotaAgente, SituacaoModelo, SituacaoNota

PALAVRAS_VAZIAS = {"para", "nos", "nós", "a", "o", "os", "as", "que", "aqui", "no", "na", "do", "da", "de",
                   "um", "uma", "lembre", "lembrar", "guarde", "anote"}
SEPARADORES = r"\s+(?:é|são|significa|quer dizer|equivale a|=)\s+|:"


def _norm(t: str) -> str:
    return unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()


def termo(texto: str) -> str:
    """Termo que a nota define ("inadimplente é ..." → "inadimplente"). Vazio se não houver definição."""
    partes = re.split(SEPARADORES, texto or "", maxsplit=1)
    if len(partes) < 2:
        return ""
    palavras = [p for p in re.findall(r"\w+", _norm(partes[0])) if p not in {_norm(x) for x in PALAVRAS_VAZIAS}]
    return " ".join(palavras[-3:])


def _nivel(setor) -> str:
    return f"setor {setor.nome}" if setor else "empresa"


def _pode_decidir(user, setor, criado_por_id) -> bool:
    if criado_por_id == user.pk and not memoria_valores.autoaprovacao():
        return False
    return tem_permissao(user, Permissao.APROVAR_CONHECIMENTO, setor)


# --- notas ---------------------------------------------------------------------------------------

def notas_validas(user, setor=None):
    setor = setor if setor is not None else setor_do_usuario(user)
    filtro = Q(escopo=EscopoNota.EMPRESA) | Q(escopo=EscopoNota.USUARIO, usuario=user)
    if setor is not None:
        filtro |= Q(escopo=EscopoNota.SETOR, setor=setor)
    return NotaAgente.objects.filter(ativo=True, situacao=SituacaoNota.APROVADA).filter(filtro)


def _conflitante(escopo, setor, usuario, texto):
    t = termo(texto)
    if not t:
        return None
    qs = NotaAgente.objects.filter(ativo=True, situacao=SituacaoNota.APROVADA, escopo=escopo, setor=setor,
                                   usuario=usuario)
    for n in qs:
        if termo(n.texto) == t:
            return n
    return None


@transaction.atomic
def lembrar(user, texto: str, alcance: str = "") -> NotaAgente:
    """Guarda uma nota. `alcance` vazio: empresa para administradores (como na M10), usuário para os demais."""
    texto = (texto or "").strip()[:300]
    if not texto:
        raise ValidationError("Nada para guardar.")
    if contem_cpf_cnpj(texto):
        raise ValidationError("Não guardo CPF ou CNPJ na memória.")
    setor_user = setor_do_usuario(user)
    if not alcance:
        alcance = EscopoNota.EMPRESA if aprendizado.pode_aprender(user) else EscopoNota.USUARIO
    if alcance not in EscopoNota.values:
        raise ValidationError("Alcance desconhecido.")
    setor = usuario = None
    if alcance == EscopoNota.USUARIO:
        usuario = user
    else:
        if setor_user is None:
            raise PermissaoNegada("Usuário sem setor ativo.")
        if alcance == EscopoNota.SETOR:
            setor = setor_user
    anterior = _conflitante(alcance, setor, usuario, texto)
    if anterior is not None and _norm(anterior.texto) == _norm(texto):
        raise ValidationError("Isso já está na memória.")
    privado = alcance == EscopoNota.USUARIO
    nota = NotaAgente.objects.create(
        texto=texto, escopo=alcance, usuario=usuario, setor=setor, criado_por=user, substitui=anterior,
        situacao=SituacaoNota.APROVADA if privado else SituacaoNota.PROPOSTA,
        aprovado_por=user if privado else None, aprovado_em=timezone.now() if privado else None,
    )
    registrar("parametro_registrado", "agente", usuario=user, setor=setor_user, entidade_id=nota.pk,
              estado_novo="nota_memoria")
    if privado:
        if anterior is not None:
            anterior.situacao = SituacaoNota.SUBSTITUIDA
            anterior.save(update_fields=["situacao"])
        return nota
    if _pode_decidir(user, setor, user.pk):  # autoaprovação ligada e com permissão no nível
        return aprovar_nota(user, nota)
    return nota


def _travar_nota(nota) -> NotaAgente:
    return NotaAgente.objects.select_for_update(of=("self",)).select_related("setor").get(pk=nota.pk)


@transaction.atomic
def aprovar_nota(user, nota) -> NotaAgente:
    nota = _travar_nota(nota)
    if nota.situacao != SituacaoNota.PROPOSTA:
        raise ValidationError("Somente propostas podem ser aprovadas.")
    if not _pode_decidir(user, nota.setor, nota.criado_por_id):
        raise PermissaoNegada("Sem permissão para aprovar esta nota (ou foi você quem a propôs).")
    nota.situacao, nota.aprovado_por, nota.aprovado_em = SituacaoNota.APROVADA, user, timezone.now()
    nota.save(update_fields=["situacao", "aprovado_por", "aprovado_em"])
    if nota.substitui_id:
        NotaAgente.objects.filter(pk=nota.substitui_id, situacao=SituacaoNota.APROVADA).update(
            situacao=SituacaoNota.SUBSTITUIDA)
    registrar("parametro_aprovado", "agente", usuario=user, setor=nota.setor, entidade_id=nota.pk,
              estado_anterior=SituacaoNota.PROPOSTA, estado_novo="nota_aprovada")
    return nota


@transaction.atomic
def rejeitar_nota(user, nota) -> NotaAgente:
    nota = _travar_nota(nota)
    if nota.situacao != SituacaoNota.PROPOSTA or not tem_permissao(user, Permissao.APROVAR_CONHECIMENTO, nota.setor):
        raise PermissaoNegada("Sem permissão para rejeitar esta nota.")
    nota.situacao = SituacaoNota.REJEITADA
    nota.save(update_fields=["situacao"])
    registrar("parametro_rejeitado", "agente", usuario=user, setor=nota.setor, entidade_id=nota.pk,
              estado_anterior=SituacaoNota.PROPOSTA, estado_novo="nota_rejeitada")
    return nota


# --- fila de aprovação ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Pendencia:
    ref: str            # "valor-12", "nota-3", "consulta-5"
    tipo: str           # "Valor padrão", "Nota", "Consulta aprendida"
    descricao: str
    nivel: str
    autor: str
    atual: str = ""     # o que vale hoje, quando a proposta substitui algo (conflito)
    pode_decidir: bool = False


def _autor(u) -> str:
    return (u.get_full_name() or u.get_username()) if u else "—"


def _desc_valor(b) -> str:
    return f"{b.definicao.nome}: {exibir(b.valor)}" + (" (regra obrigatória)" if b.obrigatoria else "")


def _setores_aprovacao(user) -> set[int]:
    return setores_autorizados(user, Permissao.APROVAR_CONHECIMENTO)


def pendencias(user) -> list[Pendencia]:
    """Propostas que o usuário pode ver na fila: as do nível em que aprova e as que ele mesmo fez."""
    setores = _setores_aprovacao(user)
    empresa = tem_permissao(user, Permissao.APROVAR_CONHECIMENTO)
    out = []
    nivel_q = Q(setor_id__in=setores) | Q(criado_por=user)
    if empresa:
        nivel_q |= Q(setor__isnull=True)
    valores = (ParameterBinding.objects.filter(situacao=Situacao.PROPOSTA, escopo__in=[Escopo.SETOR, Escopo.EMPRESA])
               .filter(nivel_q).select_related("definicao", "setor", "criado_por", "substitui").order_by("id"))
    for b in valores:
        atual = _desc_valor(b.substitui) if b.substitui and b.substitui.situacao == Situacao.APROVADA else ""
        out.append(Pendencia(f"valor-{b.pk}", "Valor padrão", _desc_valor(b), _nivel(b.setor), _autor(b.criado_por),
                             atual, _pode_decidir(user, b.setor, b.criado_por_id)))
    notas = (NotaAgente.objects.filter(situacao=SituacaoNota.PROPOSTA, ativo=True).filter(nivel_q)
             .select_related("setor", "criado_por", "substitui").order_by("id"))
    for n in notas:
        atual = n.substitui.texto if n.substitui and n.substitui.situacao == SituacaoNota.APROVADA else ""
        out.append(Pendencia(f"nota-{n.pk}", "Nota", n.texto, _nivel(n.setor), _autor(n.criado_por), atual,
                             _pode_decidir(user, n.setor, n.criado_por_id)))
    for cm in ConsultaModelo.objects.filter(situacao=SituacaoModelo.PROPOSTA).select_related("criado_por"):
        pode = aprendizado.pode_aprovar(user, cm)
        if pode or cm.criado_por_id == user.pk or empresa or cm.setores.filter(pk__in=setores).exists():
            out.append(Pendencia(f"consulta-{cm.pk}", "Consulta aprendida", f"{cm.nome} ({cm.tabela})",
                                 ", ".join(s.nome for s in cm.setores.all()) or "—", _autor(cm.criado_por), "", pode))
    return out


def achar_pendencia(user, alvo: str) -> list[Pendencia]:
    """Por referência exata ("nota-3") ou por palavras da descrição."""
    lista = pendencias(user)
    alvo_n = _norm(alvo).strip()
    exatos = [p for p in lista if p.ref == alvo_n]
    if exatos or not alvo_n:
        return exatos
    palavras = [w for w in re.findall(r"\w+", alvo_n) if len(w) >= 3 and w not in {"nota", "valor", "proposta"}]
    return [p for p in lista if palavras and all(w in _norm(p.descricao) for w in palavras)]


@transaction.atomic
def decidir(user, ref: str, aprovar: bool) -> str:
    tipo, _, pk = ref.partition("-")
    if not pk.isdigit():
        raise ValidationError("Pendência desconhecida.")
    if tipo == "valor":
        b = ParameterBinding.objects.get(pk=int(pk), escopo__in=[Escopo.SETOR, Escopo.EMPRESA])
        if aprovar:
            if not _pode_decidir(user, b.setor, b.criado_por_id):
                raise PermissaoNegada("Sem permissão para aprovar este valor (ou foi você quem o propôs).")
            memoria_valores.aprovar(user, b)
        else:
            memoria_valores.rejeitar(user, b)
        return _desc_valor(b)
    if tipo == "nota":
        n = NotaAgente.objects.get(pk=int(pk))
        (aprovar_nota if aprovar else rejeitar_nota)(user, n)
        return n.texto
    if tipo == "consulta":
        cm = ConsultaModelo.objects.get(pk=int(pk))
        (aprendizado.aprovar if aprovar else aprendizado.rejeitar)(user, cm)
        return cm.nome
    raise ValidationError("Pendência desconhecida.")


# --- visão da memória por setor -----------------------------------------------------------------

def setores_visiveis(user):
    ids = setores_autorizados(user, Permissao.CONSULTAR) | setores_autorizados(user, Permissao.LER_MEMORIA)
    return Department.objects.filter(pk__in=ids, ativo=True).order_by("nome")


def memoria_do_setor(user, setor) -> dict:
    if setor.pk not in {s.pk for s in setores_visiveis(user)}:
        raise PermissaoNegada("Sem acesso à memória deste setor.")
    valores = (ParameterBinding.objects.filter(situacao=Situacao.APROVADA)
               .filter(Q(escopo=Escopo.SETOR, setor=setor) | Q(escopo=Escopo.EMPRESA))
               .select_related("definicao", "setor", "aprovado_por").order_by("definicao__nome", "escopo"))
    agora = timezone.now()
    valores = [b for b in valores if (b.valido_ate is None or b.valido_ate > agora)
               and not b.definicao.contem_dado_pessoal]
    notas = (NotaAgente.objects.filter(ativo=True, situacao=SituacaoNota.APROVADA)
             .filter(Q(escopo=EscopoNota.SETOR, setor=setor) | Q(escopo=EscopoNota.EMPRESA)
                     | Q(escopo=EscopoNota.USUARIO, usuario=user))
             .select_related("aprovado_por", "setor"))
    consultas = ConsultaModelo.objects.filter(situacao=SituacaoModelo.APROVADA, setores=setor).select_related(
        "aprovado_por")
    return {
        "setor": setor,
        "valores": [{"nome": b.definicao.nome, "valor": exibir(b.valor), "nivel": _nivel(b.setor),
                     "obrigatoria": b.obrigatoria, "versao": b.versao, "aprovado_por": _autor(b.aprovado_por),
                     "aprovado_em": b.aprovado_em} for b in valores],
        "notas": [{"texto": n.texto, "nivel": "só você" if n.escopo == EscopoNota.USUARIO else _nivel(n.setor),
                   "aprovado_por": _autor(n.aprovado_por), "aprovado_em": n.aprovado_em} for n in notas],
        "consultas": [{"nome": cm.nome, "tabela": cm.tabela, "aprovado_por": _autor(cm.aprovado_por),
                       "aprovado_em": cm.aprovado_em} for cm in consultas],
    }
