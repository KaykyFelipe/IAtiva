"""Chat com histórico (M8): conversas individuais, do setor e mestre; acesso e retenção."""
from datetime import timedelta
from io import StringIO

import pytest
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from iativa.accounts.authz import PermissaoNegada
from iativa.accounts.models import Department
from iativa.chat import conversas
from iativa.chat.models import ConversationContext, EstadoSolicitacao as E, Mensagem, RequestContext, TipoConversa
from iativa.metadata.models import DominioSetor

pytestmark = pytest.mark.django_db
INADIMPLENTES = "Quais clientes estão inadimplentes?"


@pytest.fixture(autouse=True)
def _base(erp_sintetico, settings):
    settings.IATIVA_METADADOS = {"fonte": "sintetico"}
    call_command("seed_exemplos", stdout=StringIO())


@pytest.fixture
def setores(db):
    """Sobrescreve a fixture global: o seed já cria diretoria e ti."""
    return {c: Department.objects.get_or_create(codigo=c, defaults={"nome": c.title()})[0]
            for c in ("vendas", "diretoria", "ti")}


@pytest.fixture
def cobranca():
    return Department.objects.get(codigo="financeiro-cobranca")


@pytest.fixture
def ana(novo_usuario, politica, cobranca):
    user = novo_usuario("ana", cobranca, first_name="Ana", last_name="Souza")
    politica(user, cobranca, consultar=True, filiais=["001001"])
    return user


@pytest.fixture
def logado(client, ana):
    client.force_login(ana)
    return client


# --- Tela -----------------------------------------------------------------------------------

def test_pagina_inicial_tem_lateral_perfil_e_logo(logado):
    html = logado.get(reverse("chat:index")).content.decode()
    assert "Nova conversa" in html and "Ana Souza" in html and "Financeiro — Cobrança" in html
    assert "img/logo-ativa.png" in html and "Por onde começar" in html


def test_sem_setor_nao_cria_conversa(client, usuario):
    client.force_login(usuario)
    r = client.post(reverse("chat:nova"), {"mensagem": "Olá"})
    assert "ainda não tem setor" in r.content.decode()
    assert not ConversationContext.objects.exists()


def test_primeira_mensagem_cria_conversa_e_redireciona(logado, ana):
    r = logado.post(reverse("chat:nova"), {"mensagem": INADIMPLENTES})
    c = ConversationContext.objects.get()
    assert r.status_code == 302 and r.url == reverse("chat:conversa", args=[c.pk])
    assert c.tipo == TipoConversa.INDIVIDUAL and c.usuario == ana and c.titulo == INADIMPLENTES
    papeis = list(c.mensagens.values_list("papel", flat=True))
    assert papeis == ["usuario", "ia"]


def test_nova_por_htmx_usa_hx_redirect(logado):
    r = logado.post(reverse("chat:nova"), {"mensagem": INADIMPLENTES}, HTTP_HX_REQUEST="true")
    assert r.status_code == 204 and r["HX-Redirect"].startswith("/c/")


def test_conversa_com_varias_mensagens_e_ficha(logado, ana):
    logado.post(reverse("chat:nova"), {"mensagem": INADIMPLENTES})
    c = ConversationContext.objects.get()
    html = logado.get(reverse("chat:conversa", args=[c.pk])).content.decode()
    assert 'name="codigo" value="data-corte"' in html  # ficha editável
    url = reverse("chat:mensagem", args=[c.pk])
    r = logado.post(url, {"mensagem": "corte 31/12/2025, 5 dias, considerar saldo, filial 001001"}, HTTP_HX_REQUEST="true")
    html = r.content.decode()
    assert 'id="conversa"' in html and "<html" not in html
    assert "Todos os critérios estão definidos" in html
    assert 'name="codigo" value="data-corte"' not in html  # ficha antiga deixou de ser editável
    assert c.mensagens.count() == 4


def test_resposta_pelo_formulario_vira_mensagem(logado):
    logado.post(reverse("chat:nova"), {"mensagem": INADIMPLENTES})
    c = ConversationContext.objects.get()
    logado.post(reverse("chat:parametro", args=[c.pk]), {"codigo": "data-corte", "valor": "2025-12-31"})
    textos = list(c.mensagens.values_list("texto", flat=True))
    assert "Data de corte: 2025-12-31" in textos


def test_cancelar_na_conversa(logado):
    logado.post(reverse("chat:nova"), {"mensagem": INADIMPLENTES})
    c = ConversationContext.objects.get()
    r = logado.post(reverse("chat:cancelar", args=[c.pk]), HTTP_HX_REQUEST="true")
    assert "Solicitação cancelada" in r.content.decode()
    assert RequestContext.objects.get().estado == E.CANCELADA


def test_lateral_lista_conversas_com_estado(logado):
    logado.post(reverse("chat:nova"), {"mensagem": INADIMPLENTES})
    html = logado.get(reverse("chat:index")).content.decode()
    assert "Hoje" in html and 'class="ponto pendente"' in html


# --- Regras de armazenamento ----------------------------------------------------------------

def test_cpf_e_recusado_e_nao_gravado(logado):
    r = logado.post(reverse("chat:nova"), {"mensagem": "cliente com CPF 123.456.789-09"})
    assert "Não envie CPF ou CNPJ" in r.content.decode()
    assert not Mensagem.objects.exists()


def test_mensagem_vazia_ou_longa_e_rejeitada(logado):
    logado.post(reverse("chat:nova"), {"mensagem": "   "})
    logado.post(reverse("chat:nova"), {"mensagem": "x" * (settings.IATIVA_CHAT_MAX_CHARS + 1)})
    assert not Mensagem.objects.exists()


def test_ficha_nao_guarda_linhas_do_erp(logado):
    logado.post(reverse("chat:nova"), {"mensagem": INADIMPLENTES + " corte 31/12/2025, 5 dias, saldo, filial 001001"})
    tudo = repr(list(Mensagem.objects.values_list("texto", "ficha")))
    assert "CLIENTE SINTETICO" not in tudo and "SELECT" not in tudo.upper().replace("SELECIONE", "")


def test_expurgo_remove_conversas_paradas(ana):
    c = conversas.nova_individual(ana)
    conversas.enviar(ana, c, INADIMPLENTES)
    ConversationContext.objects.filter(pk=c.pk).update(atualizado_em=timezone.now() - timedelta(days=91))
    call_command("expurgar_retencao", stdout=StringIO())
    assert not ConversationContext.objects.filter(pk=c.pk).exists() and not Mensagem.objects.exists()


# --- Acesso ---------------------------------------------------------------------------------

def test_conversa_de_outro_usuario_da_404(logado, novo_usuario, politica, cobranca):
    bia = novo_usuario("bia", cobranca)
    politica(bia, cobranca, consultar=True)
    c = conversas.nova_individual(bia)
    conversas.enviar(bia, c, INADIMPLENTES)
    assert logado.get(reverse("chat:conversa", args=[c.pk])).status_code == 404
    assert logado.post(reverse("chat:mensagem", args=[c.pk]), {"mensagem": "oi"}).status_code == 404


def test_chat_do_setor_compartilhado(logado, ana, novo_usuario, politica, cobranca):
    bia = novo_usuario("bia", cobranca)
    politica(bia, cobranca, consultar=True)
    r = logado.get(reverse("chat:setor", args=[cobranca.pk]))
    c = ConversationContext.objects.get(tipo=TipoConversa.SETOR)
    assert r.url == reverse("chat:conversa", args=[c.pk]) and c.usuario is None
    conversas.enviar(bia, c, INADIMPLENTES)
    html = logado.get(r.url).content.decode()
    assert "bia" in html and 'class="msg pessoa"' in html
    # A ficha da Bia não é editável pela Ana.
    assert 'name="codigo"' not in html


def test_setor_leitor_nao_escreve(client, novo_usuario, politica, cobranca, setores):
    leitor = novo_usuario("gestor", setores["ti"])
    politica(leitor, cobranca, ler_memoria=True)
    client.force_login(leitor)
    c = conversas.do_setor(leitor, cobranca.pk)
    html = client.get(reverse("chat:conversa", args=[c.pk])).content.decode()
    assert "somente leitura" in html and 'name="mensagem"' not in html
    with pytest.raises(PermissaoNegada):
        conversas.enviar(leitor, c, INADIMPLENTES)


def test_setor_sem_politica_nao_ve(logado, setores):
    assert logado.get(reverse("chat:setor", args=[setores["ti"].pk])).status_code == 404


def test_chat_mestre_so_com_mais_de_um_setor(client, ana, novo_usuario, politica, cobranca, setores):
    client.force_login(ana)
    assert "Chat mestre" not in client.get(reverse("chat:index")).content.decode()
    assert client.get(reverse("chat:mestre")).status_code == 404
    diretor = novo_usuario("diretor", setores["diretoria"], funcao="diretoria")
    politica(diretor, cobranca, consultar=True)
    politica(diretor, setores["vendas"], consultar=True)
    client.force_login(diretor)
    assert "Chat mestre" in client.get(reverse("chat:index")).content.decode()
    r = client.get(reverse("chat:mestre"))
    c = ConversationContext.objects.get(tipo=TipoConversa.MESTRE)
    assert r.url == reverse("chat:conversa", args=[c.pk]) and c.usuario == diretor


def test_diretoria_sem_politicas_nao_tem_mestre(client, novo_usuario, setores):
    diretor = novo_usuario("diretor", setores["diretoria"], funcao="diretoria", is_superuser=True)
    client.force_login(diretor)
    assert client.get(reverse("chat:mestre")).status_code == 404


def test_validar_texto():
    with pytest.raises(ValidationError):
        conversas._validar_texto("CNPJ 12.345.678/0001-90")
