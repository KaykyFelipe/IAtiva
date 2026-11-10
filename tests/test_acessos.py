"""Setores, políticas de acesso e autorização resolvida no backend."""
from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from iativa.accounts.authz import (
    Permissao,
    PermissaoNegada,
    exigir,
    filiais_autorizadas,
    setor_do_usuario,
    setores_autorizados,
    tem_permissao,
)
from iativa.accounts.models import AccessPolicy
from iativa.audit.models import OperationAudit

pytestmark = pytest.mark.django_db


def test_consulta_sem_setor_nao_existe(novo_usuario, setores):
    user = novo_usuario("ana", setores["cobranca"])
    with pytest.raises(ValidationError):
        AccessPolicy.objects.create(user=user, department=None, pode_consultar=True, filiais=["01"])


def test_consulta_exige_filiais_validas(novo_usuario, setores):
    user = novo_usuario("ana", setores["cobranca"])
    with pytest.raises(ValidationError):
        AccessPolicy.objects.create(user=user, department=setores["cobranca"], pode_consultar=True, filiais=[])
    with pytest.raises(ValidationError):
        AccessPolicy.objects.create(
            user=user, department=setores["cobranca"], pode_consultar=True, filiais=["01; DROP"]
        )


def test_permissoes_sao_separadas(novo_usuario, setores, politica):
    user = novo_usuario("ana", setores["cobranca"])
    politica(user, setores["cobranca"], consultar=True)
    assert tem_permissao(user, Permissao.CONSULTAR, setores["cobranca"])
    for p in (Permissao.LER_MEMORIA, Permissao.ALTERAR_OPERACAO, Permissao.APROVAR_CONHECIMENTO, Permissao.ADMINISTRAR):
        assert not tem_permissao(user, p, setores["cobranca"])


def test_permissao_de_setor_nao_vale_para_outro_setor_nem_empresa(novo_usuario, setores, politica):
    user = novo_usuario("ana", setores["cobranca"])
    politica(user, setores["cobranca"], consultar=True, aprovar_conhecimento=True)
    assert not tem_permissao(user, Permissao.CONSULTAR, setores["vendas"])
    assert not tem_permissao(user, Permissao.APROVAR_CONHECIMENTO, None)


def test_funcao_e_superusuario_nao_concedem_acesso(novo_usuario, setores):
    diretor = novo_usuario("dir", setores["diretoria"], funcao="diretoria")
    admin_ti = novo_usuario("root", setores["ti"], funcao="ti", is_superuser=True, is_staff=True)
    for user in (diretor, admin_ti):
        assert setores_autorizados(user, Permissao.CONSULTAR) == set()
        assert not tem_permissao(user, Permissao.CONSULTAR, setores["cobranca"])
        assert not tem_permissao(user, Permissao.ADMINISTRAR, None)


def test_visao_consolidada_somente_setores_concedidos(novo_usuario, setores, politica):
    diretor = novo_usuario("dir", setores["diretoria"], funcao="diretoria")
    politica(diretor, setores["cobranca"], consultar=True, filiais=["01", "02"])
    politica(diretor, setores["vendas"], consultar=True, filiais=["03"])
    assert setores_autorizados(diretor, Permissao.CONSULTAR) == {setores["cobranca"].pk, setores["vendas"].pk}
    assert setores["ti"].pk not in setores_autorizados(diretor, Permissao.CONSULTAR)
    assert filiais_autorizadas(diretor, setores["cobranca"]) == {"01", "02"}
    assert filiais_autorizadas(diretor, setores["ti"]) == frozenset()


def test_filiais_sao_uniao_das_politicas_vigentes(novo_usuario, setores, politica):
    user = novo_usuario("ana", setores["cobranca"])
    politica(user, setores["cobranca"], consultar=True, filiais=["01"])
    politica(user, setores["cobranca"], consultar=True, filiais=["02"])
    expirada = politica(user, setores["cobranca"], consultar=True, filiais=["03"])
    AccessPolicy.objects.filter(pk=expirada.pk).update(
        valido_de=timezone.now() - timedelta(days=10), valido_ate=timezone.now() - timedelta(days=1)
    )
    assert filiais_autorizadas(user, setores["cobranca"]) == {"01", "02"}


def test_vigencia_politica(novo_usuario, setores, politica):
    user = novo_usuario("ana", setores["cobranca"])
    p = politica(user, setores["cobranca"], consultar=True)
    agora = timezone.now()
    AccessPolicy.objects.filter(pk=p.pk).update(valido_de=agora + timedelta(days=1))
    assert not tem_permissao(user, Permissao.CONSULTAR, setores["cobranca"])
    AccessPolicy.objects.filter(pk=p.pk).update(valido_de=agora - timedelta(days=2), ativo=False)
    assert not tem_permissao(user, Permissao.CONSULTAR, setores["cobranca"])
    AccessPolicy.objects.filter(pk=p.pk).update(ativo=True)
    assert tem_permissao(user, Permissao.CONSULTAR, setores["cobranca"])


def test_setor_inativo_e_usuario_inativo_perdem_acesso(novo_usuario, setores, politica):
    user = novo_usuario("ana", setores["cobranca"])
    politica(user, setores["cobranca"], consultar=True)
    setores["cobranca"].ativo = False
    setores["cobranca"].save()
    assert not tem_permissao(user, Permissao.CONSULTAR, setores["cobranca"])
    assert setor_do_usuario(user) is None
    setores["cobranca"].ativo = True
    setores["cobranca"].save()
    user.is_active = False
    user.save()
    assert not tem_permissao(user, Permissao.CONSULTAR, setores["cobranca"])


def test_exigir_nega_e_audita(novo_usuario, setores):
    user = novo_usuario("ana", setores["cobranca"])
    with pytest.raises(PermissaoNegada):
        exigir(user, Permissao.CONSULTAR, setores["vendas"])
    registro = OperationAudit.objects.get()
    assert registro.acao == "acesso_negado"
    assert registro.resultado == "negado"
    assert registro.usuario == user


def test_anonimo_nao_tem_permissao(setores):
    from django.contrib.auth.models import AnonymousUser

    assert not tem_permissao(AnonymousUser(), Permissao.CONSULTAR, setores["cobranca"])
    assert setor_do_usuario(AnonymousUser()) is None
