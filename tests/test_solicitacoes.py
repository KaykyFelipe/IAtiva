"""Contextos de solicitação, máquina de estados e checagem de completude."""
import pytest
from django.core.exceptions import ValidationError

from iativa.accounts.authz import PermissaoNegada
from iativa.accounts.models import AccessPolicy
from iativa.audit.models import OperationAudit
from iativa.chat.estados import TRANSICOES, TransicaoInvalida
from iativa.chat.models import EstadoSolicitacao as E
from iativa.chat.servicos import avaliar_completude, cancelar, criar_solicitacao, pode_ver_solicitacao, transicionar
from iativa.memory import servicos as memoria
from iativa.memory.models import Escopo

pytestmark = pytest.mark.django_db


@pytest.fixture
def ana(novo_usuario, setores, politica):
    user = novo_usuario("ana", setores["cobranca"])
    politica(user, setores["cobranca"], consultar=True)
    return user


def _nova(user):
    return criar_solicitacao(user, "financeiro_receber", "titulos-em-atraso")


def test_criacao_exige_setor_e_permissao_de_consulta(novo_usuario, django_user_model, setores):
    sem_setor = django_user_model.objects.create_user(username="x", password="senha-de-teste-123")
    with pytest.raises(PermissaoNegada):
        _nova(sem_setor)
    sem_politica = novo_usuario("y", setores["cobranca"])
    with pytest.raises(PermissaoNegada):
        _nova(sem_politica)


def test_criacao_registra_setor_do_backend_e_audita(ana, setores):
    s = _nova(ana)
    assert s.setor == setores["cobranca"] and s.estado == E.RASCUNHO
    assert OperationAudit.objects.filter(acao="solicitacao_criada", entidade_id=s.pk).exists()


@pytest.mark.parametrize("objetivo", ["quais clientes estão inadimplentes?", "SELECT * FROM SE1010", "x" * 65])
def test_objetivo_e_codigo_estruturado_nunca_texto_livre(ana, objetivo):
    with pytest.raises(ValidationError):
        criar_solicitacao(ana, "financeiro_receber", objetivo)


def test_dominio_desconhecido(ana):
    with pytest.raises(ValidationError):
        criar_solicitacao(ana, "estoque", "saldo")


def test_transicoes_validas_e_invalidas(ana):
    s = _nova(ana)
    with pytest.raises(TransicaoInvalida):
        transicionar(ana, s, E.EXECUTANDO)
    for estado in (E.VALIDANDO, E.PRONTA, E.EXECUTANDO, E.CONCLUIDA):
        s = transicionar(ana, s, estado)
    assert s.encerrado_em is not None
    with pytest.raises(TransicaoInvalida):
        transicionar(ana, s, E.AGUARDANDO_PARAMETROS)


def test_estados_finais_nao_tem_saida():
    assert TRANSICOES[E.CONCLUIDA] == frozenset() and TRANSICOES[E.CANCELADA] == frozenset()
    assert set(TRANSICOES) == set(E.values)


def test_nao_validada_exige_esclarecimento_antes_de_refazer(ana):
    s = _nova(ana)
    for estado in (E.VALIDANDO, E.PRONTA, E.EXECUTANDO, E.NAO_VALIDADA):
        s = transicionar(ana, s, estado)
    for proibido in (E.PRONTA, E.EXECUTANDO, E.CONCLUIDA):
        with pytest.raises(TransicaoInvalida):
            transicionar(ana, s, proibido)
    assert transicionar(ana, s, E.AGUARDANDO_PARAMETROS).estado == E.AGUARDANDO_PARAMETROS


def test_somente_o_dono_transiciona(ana, novo_usuario, setores):
    s = _nova(ana)
    outro = novo_usuario("bia", setores["cobranca"])
    with pytest.raises(PermissaoNegada):
        transicionar(outro, s, E.CANCELADA)


def test_cancelar_somente_em_preparacao(ana):
    s = _nova(ana)
    assert cancelar(ana, s).estado == E.CANCELADA
    s = _nova(ana)
    for estado in (E.VALIDANDO, E.PRONTA, E.EXECUTANDO):
        s = transicionar(ana, s, estado)
    with pytest.raises(TransicaoInvalida):
        cancelar(ana, s)


def test_transicoes_sao_auditadas_sem_valores(ana):
    s = _nova(ana)
    transicionar(ana, s, E.CANCELADA)
    t = OperationAudit.objects.get(acao="transicao_estado")
    assert (t.estado_anterior, t.estado_novo) == (E.RASCUNHO, E.CANCELADA)


def test_visibilidade_dono_e_compartilhada(ana, novo_usuario, setores, politica):
    s = _nova(ana)
    colega = novo_usuario("bia", setores["cobranca"])
    leitor = novo_usuario("gestor", setores["cobranca"])
    politica(leitor, setores["cobranca"], ler_memoria=True)
    assert pode_ver_solicitacao(ana, s)
    assert not pode_ver_solicitacao(colega, s)
    assert not pode_ver_solicitacao(leitor, s)
    s.compartilhado = True
    s.save()
    assert pode_ver_solicitacao(leitor, s)
    assert not pode_ver_solicitacao(colega, s)


# --- Completude (liberação determinística) -----------------------------------------------

def test_parametro_ausente_aguarda(ana, definicoes):
    s = _nova(ana)
    avaliacao = avaliar_completude(ana, s, [definicoes["data_corte"], definicoes["baixa_parcial"]])
    assert avaliacao.estado == E.AGUARDANDO_PARAMETROS
    assert {p.definicao for p in avaliacao.pendencias} == {"data-corte", "tratamento-baixa-parcial"}


def test_parametros_completos_liberam(ana, definicoes):
    s = _nova(ana)
    memoria.registrar_valor(ana, definicoes["data_corte"], Escopo.SOLICITACAO, "2025-12-31", solicitacao=s)
    memoria.registrar_valor(ana, definicoes["baixa_parcial"], Escopo.USUARIO, "considerar-saldo")
    avaliacao = avaliar_completude(ana, s, [definicoes["data_corte"], definicoes["baixa_parcial"]])
    assert avaliacao.estado == E.PRONTA
    assert {r.escopo for r in avaliacao.resolucoes} == {Escopo.SOLICITACAO, Escopo.USUARIO}


def test_perda_de_permissao_bloqueia(ana, definicoes):
    s = _nova(ana)
    AccessPolicy.objects.filter(user=ana).update(ativo=False)
    avaliacao = avaliar_completude(ana, s, [definicoes["data_corte"]])
    assert avaliacao.estado == E.BLOQUEADA
    assert OperationAudit.objects.filter(acao="acesso_negado", entidade="solicitacao").exists()


def test_conflito_impede_liberacao(ana, novo_usuario, setores, politica, definicoes):
    gestor = novo_usuario("gestor", setores["cobranca"])
    politica(gestor, setores["cobranca"], aprovar_conhecimento=True)
    d = definicoes["baixa_parcial"]
    memoria.aprovar(
        gestor,
        memoria.registrar_valor(ana, d, Escopo.SETOR, "considerar-saldo", setor=setores["cobranca"], obrigatoria=True),
    )
    s = _nova(ana)
    memoria.registrar_valor(ana, d, Escopo.SOLICITACAO, "desconsiderar-titulo", solicitacao=s)
    assert avaliar_completude(ana, s, [d]).estado == E.AGUARDANDO_PARAMETROS
