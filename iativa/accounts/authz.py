"""Autorização resolvida no backend.

Toda decisão de acesso passa por estas funções. Nada aqui é influenciado pelo modelo de IA
nem por texto recebido do navegador: a identidade vem da sessão autenticada e as permissões,
das `AccessPolicy` vigentes.
"""
from enum import StrEnum

from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.utils import timezone

from .models import AccessPolicy, Department, UserProfile


class Permissao(StrEnum):
    CONSULTAR = "pode_consultar"
    LER_MEMORIA = "pode_ler_memoria"
    ALTERAR_OPERACAO = "pode_alterar_operacao"
    APROVAR_CONHECIMENTO = "pode_aprovar_conhecimento"
    ADMINISTRAR = "pode_administrar"


class PermissaoNegada(PermissionDenied):
    pass


def _usuario_valido(user) -> bool:
    return bool(user and getattr(user, "is_authenticated", False) and user.is_active)


def setor_do_usuario(user) -> Department | None:
    if not _usuario_valido(user):
        return None
    perfil = UserProfile.objects.select_related("department").filter(user=user).first()
    if perfil is None or not perfil.department.ativo:
        return None
    return perfil.department


def politicas_vigentes(user, agora=None):
    if not _usuario_valido(user):
        return AccessPolicy.objects.none()
    agora = agora or timezone.now()
    return (
        AccessPolicy.objects.filter(user=user, ativo=True, valido_de__lte=agora)
        .filter(Q(valido_ate__isnull=True) | Q(valido_ate__gt=agora))
        .filter(Q(department__isnull=True) | Q(department__ativo=True))
    )


def tem_permissao(user, permissao: Permissao, setor: Department | None = None, agora=None) -> bool:
    """`setor=None` verifica o nível empresa. Permissão de setor não vale para a empresa e vice-versa."""
    qs = politicas_vigentes(user, agora).filter(**{Permissao(permissao).value: True})
    qs = qs.filter(department__isnull=True) if setor is None else qs.filter(department=setor)
    return qs.exists()


def setores_autorizados(user, permissao: Permissao, agora=None) -> set[int]:
    """Setores (ids) em que o usuário tem a permissão. Base da visão consolidada."""
    qs = politicas_vigentes(user, agora).filter(**{Permissao(permissao).value: True}, department__isnull=False)
    return set(qs.values_list("department_id", flat=True))


def filiais_autorizadas(user, setor: Department, agora=None) -> frozenset[str]:
    """Filiais que o usuário pode consultar no setor (união das políticas de consulta vigentes)."""
    filiais: set[str] = set()
    for lista in politicas_vigentes(user, agora).filter(pode_consultar=True, department=setor).values_list(
        "filiais", flat=True
    ):
        filiais.update(lista or [])
    return frozenset(filiais)


def exigir(user, permissao: Permissao, setor: Department | None = None, agora=None) -> None:
    if tem_permissao(user, permissao, setor, agora):
        return
    from iativa.audit.servicos import registrar

    registrar(
        "acesso_negado",
        "permissao",
        usuario=user if _usuario_valido(user) else None,
        setor=setor,
        estado_novo=Permissao(permissao).name.lower(),
        resultado="negado",
    )
    raise PermissaoNegada("Permissão insuficiente.")
