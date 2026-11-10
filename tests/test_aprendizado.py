"""M9: aprendizado assistido de novas consultas (base sintética)."""
from io import StringIO

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.http import QueryDict
from django.urls import reverse

from iativa.accounts.authz import PermissaoNegada
from iativa.accounts.models import Department
from iativa.agent import aprendizado
from iativa.agent import orquestrador as orq
from iativa.agent.models import ConsultaModelo, SituacaoModelo
from iativa.agent.provedores import FakeProvider
from iativa.chat import conversas
from iativa.chat.models import ConversationContext
from iativa.query import servico

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _base(erp_sintetico, settings):
    settings.IATIVA_METADADOS = {"fonte": "sintetico"}
    call_command("seed_exemplos", stdout=StringIO())


@pytest.fixture
def ti():
    return Department.objects.get(codigo="ti")


@pytest.fixture
def admin_ti(novo_usuario, politica, ti):
    u = novo_usuario("diego", ti)
    politica(u, ti, consultar=True, administrar=True, filiais=["01"])
    return u


@pytest.fixture
def aprovador(novo_usuario, politica, ti):
    u = novo_usuario("gestor", ti)
    politica(u, ti, consultar=True, aprovar_conhecimento=True, filiais=["01"])
    return u


def _dados(ti, **extra):
    q = QueryDict(mutable=True)
    q.update({"nome": "Títulos emitidos no período", "campo_data": "E1_EMISSAO"})
    q.setlist("colunas", ["E1_NUM", "E1_CLIENTE", "E1_EMISSAO", "E1_VALOR"])
    q.setlist("setores", [str(ti.pk)])
    for k, v in extra.items():
        q[k] = v
    return q


def test_so_administrador_aprende(novo_usuario, politica, ti):
    comum = novo_usuario("ana", ti)
    politica(comum, ti, consultar=True)
    with pytest.raises(PermissaoNegada):
        aprendizado.candidatos(comum, "emissão de títulos")


def test_candidatos_vem_do_dicionario(admin_ti):
    lista = aprendizado.candidatos(admin_ti, "quais títulos foram emitidos")
    assert lista and lista[0]["alias"] == "SE1"
    assert any(c["campo"] == "E1_EMISSAO" for c in lista[0]["campos"])
    assert aprendizado.candidatos(admin_ti, "SA1")[0]["alias"] == "SA1"  # alias citado


def test_pergunta_desconhecida_oferece_aprender(admin_ti):
    r = orq.processar_mensagem(admin_ti, "quais títulos foram emitidos ontem?", provedor=FakeProvider())
    assert r.tipo == "aprendizado" and r.candidatos[0]["alias"] == "SE1"


def test_usuario_comum_continua_fora_do_escopo(novo_usuario, politica):
    cob = Department.objects.get(codigo="financeiro-cobranca")
    u = novo_usuario("bia", cob)
    politica(u, cob, consultar=True)
    r = orq.processar_mensagem(u, "quais títulos foram emitidos ontem?", provedor=FakeProvider())
    assert r.tipo == "fora_do_escopo"


def test_proposta_valida_contra_catalogo(admin_ti, ti):
    cm = aprendizado.salvar_proposta(admin_ti, "SE1", _dados(ti))
    assert cm.situacao == SituacaoModelo.PROPOSTA and cm.codigo.startswith("aprendido-")
    assert "E1_SALDO" in cm.spec["ocultos"]  # não marcado = oculto
    with pytest.raises(ValidationError):
        aprendizado.salvar_proposta(admin_ti, "SE1", _dados(ti, filtro_campo_0="E1_TIPO", filtro_valor_0="NF'; DROP"))
    ruim = _dados(ti)
    ruim.setlist("colunas", ["E1_XNOMCLI"])  # virtual
    with pytest.raises(ValidationError):
        aprendizado.salvar_proposta(admin_ti, "SE1", ruim)
    with pytest.raises(PermissaoNegada):
        aprendizado.campos_da_tabela(admin_ti, "SX3")


def test_sem_autoaprovacao_e_aprovacao_por_outro(admin_ti, aprovador, ti, settings):
    cm = aprendizado.salvar_proposta(admin_ti, "SE1", _dados(ti))
    with pytest.raises(PermissaoNegada):
        aprendizado.aprovar(admin_ti, cm)
    aprendizado.aprovar(aprovador, cm)
    cm.refresh_from_db()
    assert cm.situacao == SituacaoModelo.APROVADA and cm.aprovado_por == aprovador


def test_autoaprovacao_quando_liberada(admin_ti, ti, politica, settings):
    settings.IATIVA_APRENDIZADO_AUTOAPROVACAO = True
    politica(admin_ti, None, aprovar_conhecimento=True)
    cm = aprendizado.salvar_proposta(admin_ti, "SE1", _dados(ti))
    assert aprendizado.aprovar(admin_ti, cm).situacao == SituacaoModelo.APROVADA


def test_consulta_aprendida_executa_e_respeita_setor(admin_ti, aprovador, ti, novo_usuario, politica):
    cm = aprendizado.salvar_proposta(admin_ti, "SE1", _dados(ti))
    aprendizado.aprovar(aprovador, cm)
    r = orq.processar_mensagem(admin_ti, "títulos emitidos de 01/09/2025 a 31/12/2025 filial 01", provedor=FakeProvider())
    assert r.tipo == "pronta", (r.tipo, r.mensagem, [p.codigo for p in r.perguntas])
    res = servico.executar_solicitacao(admin_ti, r.solicitacao)
    assert res.ok and res.colunas == ["Número" if False else c for c in res.colunas]
    assert len(res.colunas) == 4 and "E1_SALDO" not in " ".join(res.colunas)
    # Outro setor não vê a consulta.
    cob = Department.objects.get(codigo="financeiro-cobranca")
    outro = novo_usuario("ze", cob)
    politica(outro, cob, consultar=True)
    assert cm.codigo not in {o.codigo for o in orq.objetivos_para(outro)}


def test_fluxo_pelo_chat(client, admin_ti, aprovador, ti):
    client.force_login(admin_ti)
    client.post(reverse("chat:nova"), {"mensagem": "quais títulos foram emitidos ontem?"})
    c = ConversationContext.objects.get()
    html = client.get(reverse("chat:conversa", args=[c.pk])).content.decode()
    assert "Aprender com SE1" in html
    html = client.post(reverse("chat:aprender", args=[c.pk]), {"tabela": "SE1"}, HTTP_HX_REQUEST="true").content.decode()
    assert 'name="colunas" value="E1_EMISSAO"' in html and "E1_XNOMCLI" not in html
    dados = _dados(ti)
    dados["tabela"] = "SE1"
    html = client.post(reverse("chat:aprender_salvar", args=[c.pk]), dados, HTTP_HX_REQUEST="true").content.decode()
    assert "Aguardando aprovação" in html and "Aprovar</button>" not in html  # autor não aprova
    cm = ConsultaModelo.objects.get()
    client.force_login(aprovador)
    # O aprovador não vê a conversa individual do Diego: aprova pelo admin ou por uma conversa sua.
    assert client.get(reverse("chat:conversa", args=[c.pk])).status_code == 404
    c2 = conversas.nova_individual(aprovador)
    with pytest.raises(PermissaoNegada):
        conversas.decidir_aprendizado(aprovador, c2, cm.pk, True)
