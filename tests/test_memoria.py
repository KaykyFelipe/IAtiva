"""Memória de parâmetros: escopos, aprovação, validade, correções, precedência e conflitos."""
from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from iativa.accounts.authz import PermissaoNegada
from iativa.chat.servicos import criar_solicitacao, transicionar
from iativa.memory import servicos
from iativa.memory.models import Escopo, MemoryRevision, ParameterBinding, Situacao
from iativa.memory.resolucao import AUSENTE, CONFLITO, RESOLVIDO, resolver

pytestmark = pytest.mark.django_db


@pytest.fixture
def ana(novo_usuario, setores, politica):
    user = novo_usuario("ana", setores["cobranca"])
    politica(user, setores["cobranca"], consultar=True)
    return user


@pytest.fixture
def bia(novo_usuario, setores, politica):
    user = novo_usuario("bia", setores["cobranca"])
    politica(user, setores["cobranca"], consultar=True)
    return user


@pytest.fixture
def gestor(novo_usuario, setores, politica):
    user = novo_usuario("gestor", setores["cobranca"], funcao="gestor")
    politica(user, setores["cobranca"], aprovar_conhecimento=True, ler_memoria=True)
    return user


@pytest.fixture
def aprovador_empresa(novo_usuario, setores, politica):
    user = novo_usuario("ti.aprova", setores["ti"], funcao="ti")
    politica(user, None, aprovar_conhecimento=True)
    return user


def _solicitacao(user):
    return criar_solicitacao(user, "financeiro_receber", "titulos-em-atraso")


# --- Valores e dados pessoais -------------------------------------------------------------

@pytest.mark.parametrize(
    "chave,valor",
    [
        ("data_corte", "31/12/2025"),
        ("data_corte", 20251231),
        ("baixa_parcial", "opcao-inexistente"),
        ("tolerancia", True),
        ("tolerancia", "5"),
    ],
)
def test_valor_invalido_e_recusado(ana, definicoes, chave, valor):
    with pytest.raises(ValidationError):
        servicos.registrar_valor(ana, definicoes[chave], Escopo.USUARIO, valor)
    assert not ParameterBinding.objects.exists()


@pytest.mark.parametrize("valor", ["123.456.789-09", "12345678909", "12.345.678/0001-95", "cliente 12345678000195"])
def test_cpf_cnpj_nunca_persistidos(ana, definicoes, valor):
    solicitacao = _solicitacao(ana)
    with pytest.raises(ValidationError):
        servicos.registrar_valor(ana, definicoes["cliente"], Escopo.SOLICITACAO, valor, solicitacao=solicitacao)
    assert not ParameterBinding.objects.exists()


def test_dado_pessoal_so_na_solicitacao_e_com_expiracao(ana, setores, definicoes):
    for escopo, extra in ((Escopo.USUARIO, {}), (Escopo.SETOR, {"setor": setores["cobranca"]}), (Escopo.EMPRESA, {})):
        with pytest.raises(ValidationError):
            servicos.registrar_valor(ana, definicoes["cliente"], escopo, "000001-01", **extra)
    solicitacao = _solicitacao(ana)
    b = servicos.registrar_valor(ana, definicoes["cliente"], Escopo.SOLICITACAO, "000001-01", solicitacao=solicitacao)
    esperado = timezone.now() + timedelta(days=30)
    assert abs((b.expira_em - esperado).total_seconds()) < 60


def test_escopo_incoerente_e_barrado_pelo_banco(ana, definicoes, setores):
    with pytest.raises(IntegrityError), transaction.atomic():
        ParameterBinding.objects.create(
            definicao=definicoes["data_corte"], escopo=Escopo.EMPRESA, setor=setores["cobranca"],
            valor="2026-01-01", situacao=Situacao.APROVADA,
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        ParameterBinding.objects.create(
            definicao=definicoes["data_corte"], escopo=Escopo.USUARIO, usuario=ana, obrigatoria=True,
            valor="2026-01-01", situacao=Situacao.APROVADA,
        )


# --- Definição x valor da solicitação -----------------------------------------------------

def test_valor_da_solicitacao_nao_vira_padrao(ana, definicoes):
    s1 = _solicitacao(ana)
    servicos.registrar_valor(ana, definicoes["data_corte"], Escopo.SOLICITACAO, "2025-12-31", solicitacao=s1)
    assert resolver(definicoes["data_corte"], ana, s1).valor == "2025-12-31"
    s2 = _solicitacao(ana)
    assert resolver(definicoes["data_corte"], ana, s2).situacao == AUSENTE
    assert resolver(definicoes["data_corte"], ana).situacao == AUSENTE


def test_so_o_dono_informa_parametro_da_solicitacao(ana, bia, definicoes):
    s = _solicitacao(ana)
    with pytest.raises(PermissaoNegada):
        servicos.registrar_valor(bia, definicoes["data_corte"], Escopo.SOLICITACAO, "2025-12-31", solicitacao=s)


def test_solicitacao_encerrada_nao_recebe_parametros(ana, definicoes):
    s = _solicitacao(ana)
    s = transicionar(ana, s, "cancelada")
    with pytest.raises(ValidationError):
        servicos.registrar_valor(ana, definicoes["data_corte"], Escopo.SOLICITACAO, "2025-12-31", solicitacao=s)


# --- Separação entre usuários e setores ---------------------------------------------------

def test_memoria_privada_nao_vaza_para_outro_usuario(ana, bia, definicoes):
    servicos.registrar_valor(ana, definicoes["tolerancia"], Escopo.USUARIO, 5)
    assert resolver(definicoes["tolerancia"], ana).valor == 5
    assert resolver(definicoes["tolerancia"], bia).situacao == AUSENTE


def test_memoria_de_setor_nao_vaza_para_outro_setor(ana, gestor, novo_usuario, setores, definicoes):
    proposta = servicos.registrar_valor(ana, definicoes["tolerancia"], Escopo.SETOR, 3, setor=setores["cobranca"])
    servicos.aprovar(gestor, proposta)
    vendedor = novo_usuario("vendedor", setores["vendas"])
    assert resolver(definicoes["tolerancia"], ana).valor == 3
    assert resolver(definicoes["tolerancia"], vendedor).situacao == AUSENTE


def test_nao_propoe_para_setor_alheio_sem_permissao(ana, setores, definicoes):
    with pytest.raises(PermissaoNegada):
        servicos.registrar_valor(ana, definicoes["tolerancia"], Escopo.SETOR, 3, setor=setores["vendas"])


# --- Aprovação ---------------------------------------------------------------------------

def test_afirmacao_no_chat_nao_vira_regra_oficial(ana, setores, definicoes):
    b = servicos.registrar_valor(ana, definicoes["tolerancia"], Escopo.SETOR, 3, setor=setores["cobranca"])
    assert b.situacao == Situacao.PROPOSTA
    assert resolver(definicoes["tolerancia"], ana).situacao == AUSENTE


def test_aprovacao_exige_permissao_e_segregacao(ana, bia, gestor, setores, definicoes, politica):
    b = servicos.registrar_valor(ana, definicoes["tolerancia"], Escopo.SETOR, 3, setor=setores["cobranca"])
    with pytest.raises(PermissaoNegada):
        servicos.aprovar(bia, b)
    proposta_do_gestor = servicos.registrar_valor(
        gestor, definicoes["data_corte"], Escopo.SETOR, "2026-01-01", setor=setores["cobranca"]
    )
    with pytest.raises(PermissaoNegada):
        servicos.aprovar(gestor, proposta_do_gestor)
    b = servicos.aprovar(gestor, b)
    assert b.situacao == Situacao.APROVADA and b.aprovado_por == gestor
    assert list(b.revisoes.values_list("acao", flat=True)) == ["criada", "aprovada"]


def test_aprovacao_de_empresa_exige_politica_de_empresa(ana, gestor, aprovador_empresa, definicoes):
    b = servicos.registrar_valor(ana, definicoes["baixa_parcial"], Escopo.EMPRESA, "considerar-saldo")
    with pytest.raises(PermissaoNegada):
        servicos.aprovar(gestor, b)  # aprova no setor, não na empresa
    servicos.aprovar(aprovador_empresa, b)
    assert resolver(definicoes["baixa_parcial"], ana).escopo == Escopo.EMPRESA


def test_rejeicao(ana, gestor, setores, definicoes):
    b = servicos.registrar_valor(ana, definicoes["tolerancia"], Escopo.SETOR, 3, setor=setores["cobranca"])
    b = servicos.rejeitar(gestor, b, "sem respaldo na política")
    assert b.situacao == Situacao.REJEITADA
    with pytest.raises(ValidationError):
        servicos.aprovar(gestor, b)


# --- Validade, correção e revogação ------------------------------------------------------

def test_valor_expirado_e_ignorado(ana, definicoes):
    b = servicos.registrar_valor(
        ana, definicoes["tolerancia"], Escopo.USUARIO, 5, valido_ate=timezone.now() + timedelta(days=1)
    )
    assert resolver(definicoes["tolerancia"], ana).situacao == RESOLVIDO
    ParameterBinding.objects.filter(pk=b.pk).update(valido_ate=timezone.now() - timedelta(seconds=1))
    assert resolver(definicoes["tolerancia"], ana).situacao == AUSENTE


def test_validade_no_passado_e_recusada(ana, definicoes):
    with pytest.raises(ValidationError):
        servicos.registrar_valor(
            ana, definicoes["tolerancia"], Escopo.USUARIO, 5, valido_ate=timezone.now() - timedelta(days=1)
        )


def test_correcao_privada_rastreavel(ana, definicoes):
    v1 = servicos.registrar_valor(ana, definicoes["tolerancia"], Escopo.USUARIO, 5)
    v2 = servicos.corrigir(ana, v1, 7, "valor estava errado")
    v1.refresh_from_db()
    assert v1.situacao == Situacao.SUBSTITUIDA
    assert v2.substitui == v1 and v2.versao == 2
    assert resolver(definicoes["tolerancia"], ana).valor == 7
    assert MemoryRevision.objects.filter(binding=v1, acao="corrigida").exists()


def test_correcao_compartilhada_mantem_anterior_ate_aprovar(ana, gestor, setores, definicoes):
    v1 = servicos.aprovar(
        gestor, servicos.registrar_valor(ana, definicoes["tolerancia"], Escopo.SETOR, 3, setor=setores["cobranca"])
    )
    v2 = servicos.corrigir(ana, v1, 10)
    assert v2.situacao == Situacao.PROPOSTA
    assert resolver(definicoes["tolerancia"], ana).valor == 3
    servicos.aprovar(gestor, v2)
    v1.refresh_from_db()
    assert v1.situacao == Situacao.SUBSTITUIDA
    assert resolver(definicoes["tolerancia"], ana).valor == 10


def test_nao_corrige_memoria_privada_alheia(ana, bia, definicoes):
    v1 = servicos.registrar_valor(ana, definicoes["tolerancia"], Escopo.USUARIO, 5)
    with pytest.raises(PermissaoNegada):
        servicos.corrigir(bia, v1, 9)
    with pytest.raises(PermissaoNegada):
        servicos.revogar(bia, v1)


def test_revogacao(ana, definicoes):
    v1 = servicos.registrar_valor(ana, definicoes["tolerancia"], Escopo.USUARIO, 5)
    servicos.revogar(ana, v1, "não uso mais")
    assert resolver(definicoes["tolerancia"], ana).situacao == AUSENTE


# --- Precedência e conflitos (D12) -------------------------------------------------------

@pytest.fixture
def niveis(ana, gestor, aprovador_empresa, setores, definicoes):
    d = definicoes["tolerancia"]
    servicos.aprovar(aprovador_empresa, servicos.registrar_valor(ana, d, Escopo.EMPRESA, 1))
    servicos.aprovar(gestor, servicos.registrar_valor(ana, d, Escopo.SETOR, 2, setor=setores["cobranca"]))
    return d


def test_precedencia_solicitacao_usuario_setor_empresa(ana, bia, niveis):
    r = resolver(niveis, bia)
    assert (r.valor, r.escopo) == (2, Escopo.SETOR)
    servicos.registrar_valor(ana, niveis, Escopo.USUARIO, 3)
    r = resolver(niveis, ana)
    assert (r.valor, r.escopo) == (3, Escopo.USUARIO)
    s = _solicitacao(ana)
    servicos.registrar_valor(ana, niveis, Escopo.SOLICITACAO, 4, solicitacao=s)
    r = resolver(niveis, ana, s)
    assert (r.valor, r.escopo, r.binding_id is not None) == (4, Escopo.SOLICITACAO, True)


def test_regra_obrigatoria_nao_e_sobreposta_por_preferencia(ana, gestor, setores, definicoes):
    d = definicoes["baixa_parcial"]
    regra = servicos.registrar_valor(
        ana, d, Escopo.SETOR, "considerar-saldo", setor=setores["cobranca"], obrigatoria=True
    )
    servicos.aprovar(gestor, regra)
    servicos.registrar_valor(ana, d, Escopo.USUARIO, "desconsiderar-titulo")
    r = resolver(d, ana)
    assert (r.valor, r.motivo) == ("considerar-saldo", "regra_obrigatoria")


def test_solicitacao_contra_regra_obrigatoria_gera_conflito(ana, gestor, setores, definicoes):
    d = definicoes["baixa_parcial"]
    servicos.aprovar(
        gestor,
        servicos.registrar_valor(ana, d, Escopo.SETOR, "considerar-saldo", setor=setores["cobranca"], obrigatoria=True),
    )
    s = _solicitacao(ana)
    servicos.registrar_valor(ana, d, Escopo.SOLICITACAO, "desconsiderar-titulo", solicitacao=s)
    assert resolver(d, ana, s).situacao == CONFLITO


def test_obrigatorias_divergentes_geram_conflito(ana, gestor, aprovador_empresa, setores, definicoes):
    d = definicoes["baixa_parcial"]
    servicos.aprovar(
        aprovador_empresa, servicos.registrar_valor(ana, d, Escopo.EMPRESA, "considerar-saldo", obrigatoria=True)
    )
    servicos.aprovar(
        gestor,
        servicos.registrar_valor(
            ana, d, Escopo.SETOR, "desconsiderar-titulo", setor=setores["cobranca"], obrigatoria=True
        ),
    )
    assert resolver(d, ana).motivo == "obrigatorias_divergentes"


def test_conflito_no_mesmo_nivel(ana, bia, gestor, setores, definicoes):
    d = definicoes["tolerancia"]
    servicos.aprovar(gestor, servicos.registrar_valor(ana, d, Escopo.SETOR, 2, setor=setores["cobranca"]))
    servicos.aprovar(gestor, servicos.registrar_valor(bia, d, Escopo.SETOR, 5, setor=setores["cobranca"]))
    r = resolver(d, ana)
    assert r.situacao == CONFLITO and r.escopo == Escopo.SETOR
