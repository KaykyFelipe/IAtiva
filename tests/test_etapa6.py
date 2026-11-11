"""Etapa 6: memória setorial com aprovação, fila única, conflitos e visão consolidada do chat mestre."""
from io import StringIO

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.urls import reverse

from iativa.accounts.authz import PermissaoNegada
from iativa.accounts.models import Department
from iativa.agent import memoria
from iativa.agent.models import EscopoNota, NotaAgente, SituacaoNota
from iativa.chat import conversas
from iativa.chat.models import EstadoSolicitacao as E, RequestContext
from iativa.memory import servicos
from iativa.memory.models import Escopo, ParameterDefinition
from iativa.metadata.models import DominioSetor
from iativa.query.consolidado import Consolidado

pytestmark = pytest.mark.django_db
ATRASO = "inadimplentes corte 2026-01-01, 0 dias, considerar saldo"


@pytest.fixture(autouse=True)
def _base(erp_sintetico, settings):
    settings.IATIVA_METADADOS = {"fonte": "sintetico"}
    settings.IATIVA_APRENDIZADO_AUTOAPROVACAO = False
    call_command("seed_exemplos", stdout=StringIO())


@pytest.fixture
def cob():
    return Department.objects.get(codigo="financeiro-cobranca")


@pytest.fixture
def dire():
    setor = Department.objects.get(codigo="diretoria")
    DominioSetor.objects.get_or_create(dominio="financeiro_receber", setor=setor)
    return setor


@pytest.fixture
def ana(novo_usuario, politica, cob):
    u = novo_usuario("ana", cob)
    politica(u, cob, consultar=True, filiais=["01"])
    return u


@pytest.fixture
def gestor(novo_usuario, politica, cob):
    u = novo_usuario("gestor", cob)
    politica(u, cob, consultar=True, aprovar_conhecimento=True, filiais=["01"])
    return u


# --- notas de setor -----------------------------------------------------------------------------

def test_nota_do_usuario_vale_na_hora_e_a_do_setor_so_depois_de_aprovada(ana, gestor, cob):
    privada = memoria.lembrar(ana, "prefiro ver valores em milhares", "usuario")
    assert privada.situacao == SituacaoNota.APROVADA
    proposta = memoria.lembrar(ana, "inadimplente é atraso acima de 5 dias", "setor")
    assert proposta.situacao == SituacaoNota.PROPOSTA and proposta.setor == cob
    assert proposta not in memoria.notas_validas(gestor)
    memoria.aprovar_nota(gestor, proposta)
    assert proposta in memoria.notas_validas(gestor) and proposta in memoria.notas_validas(ana)


def test_nota_do_setor_nao_vaza_para_outro_setor(ana, gestor, novo_usuario, politica, dire):
    nota = memoria.aprovar_nota(gestor, memoria.lembrar(ana, "carteira é o conjunto de títulos em aberto", "setor"))
    outro = novo_usuario("dir", dire)
    politica(outro, dire, consultar=True, filiais=["02"])
    assert nota not in memoria.notas_validas(outro)


def test_autor_e_sem_permissao_nao_aprovam(ana, gestor, settings):
    proposta = memoria.lembrar(gestor, "atraso conta do vencimento real", "setor")
    with pytest.raises(PermissaoNegada):
        memoria.aprovar_nota(gestor, proposta)  # autor
    with pytest.raises(PermissaoNegada):
        memoria.aprovar_nota(ana, proposta)  # sem "aprovar definições"
    settings.IATIVA_APRENDIZADO_AUTOAPROVACAO = True
    assert memoria.lembrar(gestor, "vencido é título com vencimento real passado", "setor").situacao == SituacaoNota.APROVADA


def test_conflito_de_nota_substitui_ao_aprovar(ana, gestor):
    antiga = memoria.aprovar_nota(gestor, memoria.lembrar(ana, "inadimplente é atraso acima de 5 dias", "setor"))
    nova = memoria.lembrar(ana, "Inadimplente é atraso acima de 10 dias", "setor")
    assert nova.substitui == antiga
    assert [p.atual for p in memoria.pendencias(gestor) if p.ref == f"nota-{nova.pk}"] == [antiga.texto]
    assert antiga in memoria.notas_validas(ana)  # até a decisão vale a aprovada
    memoria.aprovar_nota(gestor, nova)
    antiga.refresh_from_db()
    assert antiga.situacao == SituacaoNota.SUBSTITUIDA and nova in memoria.notas_validas(ana)


def test_nota_repetida_e_cpf_recusados(ana, gestor):
    memoria.aprovar_nota(gestor, memoria.lembrar(ana, "carteira é títulos em aberto", "setor"))
    with pytest.raises(ValidationError):
        memoria.lembrar(ana, "Carteira é títulos em aberto", "setor")
    with pytest.raises(ValidationError):
        memoria.lembrar(ana, "cliente 123.456.789-09 é especial", "usuario")


def test_rejeitar_nota(ana, gestor):
    p = memoria.lembrar(ana, "posição é saldo por cliente", "setor")
    memoria.decidir(gestor, f"nota-{p.pk}", aprovar=False)
    p.refresh_from_db()
    assert p.situacao == SituacaoNota.REJEITADA and p not in memoria.notas_validas(ana)


# --- fila única ---------------------------------------------------------------------------------

def test_fila_junta_valores_e_notas_e_respeita_o_nivel(ana, gestor, cob, novo_usuario, politica, dire):
    d = ParameterDefinition.objects.get(codigo="dias-tolerancia-atraso")
    servicos.registrar_valor(ana, d, Escopo.SETOR, 3, setor=cob)
    memoria.lembrar(ana, "título vencido é atraso maior que zero", "setor")
    refs = {p.ref.split("-")[0] for p in memoria.pendencias(gestor) if p.pode_decidir}
    assert refs == {"valor", "nota"}
    estranho = novo_usuario("estranho", dire)
    politica(estranho, dire, consultar=True, aprovar_conhecimento=True, filiais=["02"])
    assert memoria.pendencias(estranho) == []  # aprova em outro setor: não vê
    assert all(not p.pode_decidir for p in memoria.pendencias(ana))  # autora vê, mas não decide


def test_aprovar_valor_pela_fila(ana, gestor, cob):
    d = ParameterDefinition.objects.get(codigo="dias-tolerancia-atraso")
    b = servicos.registrar_valor(ana, d, Escopo.SETOR, 3, setor=cob)
    memoria.decidir(gestor, f"valor-{b.pk}", aprovar=True)
    b.refresh_from_db()
    assert b.situacao == "aprovada"


# --- pelo chat ----------------------------------------------------------------------------------

def test_agente_lembra_para_o_setor_e_aprova_com_confirmacao(ana, gestor):
    c = conversas.nova_individual(ana)
    msg, _ = conversas.enviar(ana, c, "lembre para o setor que inadimplente é atraso acima de 5 dias")
    assert "proposta" in msg.texto and "aprovada" in msg.texto
    nota = NotaAgente.objects.get(escopo=EscopoNota.SETOR)
    c2 = conversas.nova_individual(gestor)
    msg, _ = conversas.enviar(gestor, c2, "quais propostas estão pendentes?")
    assert f"nota-{nota.pk}" in msg.texto
    msg, _ = conversas.enviar(gestor, c2, f"aprovar nota-{nota.pk}")
    assert "Confirma" in msg.texto and msg.ficha["opcoes"][0] == f"Sim, aprovar nota-{nota.pk}"
    nota.refresh_from_db()
    assert nota.situacao == SituacaoNota.PROPOSTA
    msg, _ = conversas.enviar(gestor, c2, msg.ficha["opcoes"][0])
    nota.refresh_from_db()
    assert nota.situacao == SituacaoNota.APROVADA and "já vale" in msg.texto


def test_agente_nao_deixa_autor_aprovar(ana):
    c = conversas.nova_individual(ana)
    conversas.enviar(ana, c, "lembre para o setor que carteira é títulos em aberto")
    nota = NotaAgente.objects.get()
    msg, _ = conversas.enviar(ana, c, f"sim, aprovar nota-{nota.pk}")
    assert "não pode aprovar" in msg.texto
    nota.refresh_from_db()
    assert nota.situacao == SituacaoNota.PROPOSTA


# --- tela Memória ------------------------------------------------------------------------------

def test_tela_memoria_mostra_o_setor_e_decide(client, ana, gestor, cob):
    p = memoria.lembrar(ana, "carteira é títulos em aberto", "setor")
    client.force_login(gestor)
    r = client.get(reverse("chat:memoria"))
    assert r.status_code == 200 and "carteira é títulos em aberto" in r.content.decode()
    r = client.post(reverse("chat:memoria_decidir"), {"ref": f"nota-{p.pk}", "acao": "aprovar"})
    assert "aprovada" in r.content.decode()
    p.refresh_from_db()
    assert p.situacao == SituacaoNota.APROVADA


def test_tela_memoria_recusa_decisao_sem_permissao(client, ana, gestor):
    p = memoria.lembrar(gestor, "carteira é títulos em aberto", "setor")
    client.force_login(ana)
    r = client.post(reverse("chat:memoria_decidir"), {"ref": f"nota-{p.pk}", "acao": "aprovar"})
    assert "Proposta indisponível" in r.content.decode() or "Sem permissão" in r.content.decode()
    p.refresh_from_db()
    assert p.situacao == SituacaoNota.PROPOSTA


def test_tela_memoria_nao_mostra_setor_alheio(client, ana, dire):
    client.force_login(ana)
    r = client.get(reverse("chat:memoria"), {"setor": dire.pk})
    html = r.content.decode()
    assert r.status_code == 200 and "Diretoria" not in html


# --- chat mestre consolidado ------------------------------------------------------------------

@pytest.fixture
def diretor(novo_usuario, politica, cob, dire, settings):
    settings.IATIVA_APRENDIZADO_AUTOAPROVACAO = True
    u = novo_usuario("diretor", dire)
    politica(u, cob, consultar=True, aprovar_conhecimento=True, filiais=["01"])
    politica(u, dire, consultar=True, aprovar_conhecimento=True, filiais=["02"])
    filial = ParameterDefinition.objects.get(codigo="filial-analise")
    for setor, cod in ((cob, "01"), (dire, "02")):
        servicos.aprovar(u, servicos.registrar_valor(u, filial, Escopo.SETOR, cod, setor=setor))
    return u


def _mestre(user, texto=ATRASO):
    c = conversas.mestre(user)
    msg, res = conversas.enviar(user, c, texto)
    return msg, res


def test_mestre_roda_por_setor_e_soma_quando_premissas_iguais(diretor):
    msg, res = _mestre(diretor)
    assert isinstance(res, Consolidado) and len(res.blocos) == 2
    assert {b.setor for b in res.blocos} == {"Financeiro — Cobrança", "Diretoria"}
    assert all(b.resultado is not None for b in res.blocos)
    filiais = {b.valores["filial-analise"][1] for b in res.blocos}
    assert len(filiais) == 2 and res.somado and "Consolidado de 2 setores" in res.resumo
    assert "2 setor" in msg.texto
    # Nada pendente na conversa e nenhum resultado no histórico.
    assert not RequestContext.objects.filter(estado__in=[E.AGUARDANDO_PARAMETROS, E.PRONTA]).exists()
    assert "SINTETICO" not in msg.texto


def test_mestre_nao_soma_premissas_diferentes(diretor, cob, dire):
    d = ParameterDefinition.objects.get(codigo="tratamento-baixa-parcial")
    servicos.aprovar(diretor, servicos.registrar_valor(diretor, d, Escopo.SETOR, "desconsiderar-titulo", setor=cob))
    servicos.aprovar(diretor, servicos.registrar_valor(diretor, d, Escopo.SETOR, "considerar-saldo", setor=dire))
    _, res = _mestre(diretor, "inadimplentes corte 2026-01-01, 0 dias")
    assert not res.somado and "premissas são diferentes" in res.resumo


def test_mestre_ignora_setor_sem_consultar(novo_usuario, politica, cob, dire, settings):
    ti = Department.objects.get(codigo="ti")
    DominioSetor.objects.get_or_create(dominio="financeiro_receber", setor=ti)
    u = novo_usuario("misto", cob)
    politica(u, cob, consultar=True, filiais=["01"])
    politica(u, ti, consultar=True, filiais=["03"])
    politica(u, dire, ler_memoria=True)
    _, res = _mestre(u, ATRASO + ", filial 01")
    assert {b.setor for b in res.blocos} == {"Financeiro — Cobrança", "TI"}
    ti_bloco = next(b for b in res.blocos if b.setor == "TI")
    assert ti_bloco.resultado is None  # filial 01 não está liberada para ele no TI


def test_conversa_individual_nao_consolida(ana):
    c = conversas.nova_individual(ana)
    _, res = conversas.enviar(ana, c, ATRASO + ", filial 01")
    assert res is not None and not isinstance(res, Consolidado)
