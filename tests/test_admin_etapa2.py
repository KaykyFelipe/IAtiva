"""Páginas do admin das apps da Etapa 2 abrem e não permitem editar o que é só leitura."""
import pytest
from django.urls import reverse

from iativa.memory import servicos as memoria
from iativa.memory.models import Escopo, Situacao

pytestmark = pytest.mark.django_db

MODELOS = [
    "iativa_accounts_department",
    "iativa_accounts_userprofile",
    "iativa_accounts_accesspolicy",
    "iativa_memory_parameterdefinition",
    "iativa_memory_parameterbinding",
    "iativa_chat_conversationcontext",
    "iativa_chat_requestcontext",
    "iativa_audit_operationaudit",
    "iativa_metadata_dominiosetor",
    "iativa_metadata_restricaometadado",
]


@pytest.fixture
def admin_client_ti(client, novo_usuario, setores):
    user = novo_usuario("admin.ti", setores["ti"], funcao="ti", is_staff=True, is_superuser=True)
    client.force_login(user)
    return client, user


@pytest.mark.parametrize("nome", MODELOS)
def test_listagens_abrem(admin_client_ti, nome):
    client, _ = admin_client_ti
    assert client.get(reverse(f"admin:{nome}_changelist")).status_code == 200


@pytest.mark.parametrize("nome", ["iativa_memory_parameterbinding", "iativa_audit_operationaudit"])
def test_sem_inclusao_manual(admin_client_ti, nome):
    client, _ = admin_client_ti
    assert client.get(reverse(f"admin:{nome}_add")).status_code == 403


def test_acao_de_aprovar_respeita_politica(admin_client_ti, novo_usuario, setores, politica, definicoes):
    client, admin_ti = admin_client_ti
    ana = novo_usuario("ana", setores["cobranca"])
    proposta = memoria.registrar_valor(ana, definicoes["tolerancia"], Escopo.SETOR, 3, setor=setores["cobranca"])
    url = reverse("admin:iativa_memory_parameterbinding_changelist")
    dados = {"action": "aprovar_propostas", "_selected_action": [proposta.pk]}

    client.post(url, dados)  # superusuário sem política: recusado
    proposta.refresh_from_db()
    assert proposta.situacao == Situacao.PROPOSTA

    politica(admin_ti, setores["cobranca"], aprovar_conhecimento=True)
    client.post(url, dados)
    proposta.refresh_from_db()
    assert proposta.situacao == Situacao.APROVADA
