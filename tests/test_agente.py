"""Agente (Etapa 4): interpretação estruturada, contratos, esclarecimentos e falhas do modelo.

Usa o provedor falso e a base sintética. Provedores simulados validam contratos e fluxo, não a
capacidade real de interpretação (PEDIDO_INICIAL §16).
"""
from io import StringIO
from types import SimpleNamespace

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.db import connection
from django.urls import reverse

from iativa.agent import orquestrador as orq
from iativa.agent.conversao import converter
from iativa.agent.esquema import SaidaInvalida, esquema_json, validar
from iativa.agent.provedores import AnthropicProvider, FakeProvider, FalhaModelo
from iativa.audit.models import OperationAudit
from iativa.chat.models import EstadoSolicitacao as E, RequestContext
from iativa.memory import servicos as memoria
from iativa.memory.models import Escopo, ParameterDefinition, TipoValor
from iativa.metadata.models import DominioSetor

pytestmark = pytest.mark.django_db
INADIMPLENTES = "quais clientes estão inadimplentes?"


@pytest.fixture(autouse=True)
def _base(erp_sintetico, settings):
    settings.IATIVA_METADADOS = {"fonte": "sintetico"}
    call_command("seed_exemplos", stdout=StringIO())


@pytest.fixture
def ana(novo_usuario, politica):
    from iativa.accounts.models import Department

    setor = Department.objects.get(codigo="financeiro-cobranca")
    user = novo_usuario("ana", setor)
    politica(user, setor, consultar=True, filiais=["001001", "001007"])
    return user


def _def(codigo):
    return ParameterDefinition.objects.get(codigo=codigo)


def _msg(user, texto, sid=None, provedor=None):
    return orq.processar_mensagem(user, texto, sid, provedor or FakeProvider())


def _saida(**extra):
    base = {"intencao": "nova_consulta", "objetivo": "titulos-em-atraso", "parametros": [],
            "ambiguidade": {"pergunta": "", "opcoes": []}, "observacao": ""}
    return {**base, **extra}


# --- Interpretação ---------------------------------------------------------------------------

def test_pergunta_vaga_nao_cria_solicitacao(ana):
    r = _msg(ana, "oi, tudo bem?")
    assert r.tipo == "ajuda" and "Posso ajudar" in r.mensagem
    assert not RequestContext.objects.exists()


def test_fora_do_escopo(ana):
    r = _msg(ana, "qual o estoque do produto X?")
    assert r.tipo == "fora_do_escopo" and not RequestContext.objects.exists()


def test_inadimplencia_pergunta_so_o_que_falta(ana):
    r = _msg(ana, INADIMPLENTES)
    assert r.tipo == "pergunta" and r.solicitacao.estado == E.AGUARDANDO_PARAMETROS
    assert {p.codigo for p in r.perguntas} == {
        "data-corte", "dias-tolerancia-atraso", "tratamento-baixa-parcial", "filial-analise"
    }
    filial = next(p for p in r.perguntas if p.codigo == "filial-analise")
    assert filial.opcoes == ["001001", "001007"]  # só as autorizadas


def test_aproveita_informacao_ja_confirmada(ana):
    memoria.registrar_valor(ana, _def("dias-tolerancia-atraso"), Escopo.USUARIO, 5)
    r = _msg(ana, INADIMPLENTES)
    assert "dias-tolerancia-atraso" not in {p.codigo for p in r.perguntas}
    assert any(c.origem == "sua preferência salva" for c in r.criterios)


def test_fluxo_completo_ate_pronta(ana):
    r = _msg(ana, INADIMPLENTES)
    r = _msg(ana, "data de corte 31/12/2025, 5 dias, considerar saldo, filial 001001", r.solicitacao.pk)
    assert r.tipo == "pronta" and r.solicitacao.estado == E.PRONTA
    valores = {c.nome: c.valor for c in r.criterios}
    assert valores["Data de corte"] == "31/12/2025"
    assert all(c.origem == "informado nesta solicitação" for c in r.criterios)


def test_correcao_troca_valor_e_continua_pronta(ana):
    r = _msg(ana, INADIMPLENTES + " corte 31/12/2025, 5 dias, saldo, filial 001001")
    assert r.tipo == "pronta"
    r = _msg(ana, "na verdade 10 dias", r.solicitacao.pk)
    assert r.tipo == "pronta"
    assert {c.nome: c.valor for c in r.criterios}["Dias de tolerância de atraso"] == "10"


def test_filial_nao_autorizada_e_recusada(ana):
    r = _msg(ana, INADIMPLENTES + " filial 002001")
    assert any("Filial fora" in e for e in r.erros)
    assert "filial-analise" in {p.codigo for p in r.perguntas}


def test_mudanca_de_assunto_encerra_a_anterior(ana):
    r1 = _msg(ana, INADIMPLENTES)
    r2 = _msg(ana, "títulos a vencer de 01/01/2026 a 31/01/2026", r1.solicitacao.pk)
    assert r2.solicitacao.pk != r1.solicitacao.pk and r2.solicitacao.objetivo == "titulos-a-vencer"
    r1.solicitacao.refresh_from_db()
    assert r1.solicitacao.estado == E.CANCELADA
    assert "periodo-analise" not in {p.codigo for p in r2.perguntas}


def test_cancelar(ana):
    r = _msg(ana, INADIMPLENTES)
    r = _msg(ana, "cancela isso", r.solicitacao.pk)
    assert r.tipo == "cancelada" and r.solicitacao.estado == E.CANCELADA


def test_ambiguidade_pergunta_antes(ana):
    saida = _saida(ambiguidade={"pergunta": "Considerar só clientes ativos?", "opcoes": ["sim", "não"]})
    r = _msg(ana, INADIMPLENTES, provedor=FakeProvider([saida]))
    assert r.tipo == "pergunta" and r.ambiguidade and r.opcoes == ["sim", "não"]
    assert r.solicitacao.estado == E.AGUARDANDO_PARAMETROS


def test_parametro_fora_do_contrato_e_ignorado(ana):
    saida = _saida(parametros=[{"codigo": "periodo-analise", "valor": "01/01/2026..31/01/2026"}])
    from iativa.memory.models import ParameterBinding

    r = _msg(ana, INADIMPLENTES, provedor=FakeProvider([saida]))
    assert not ParameterBinding.objects.filter(solicitacao=r.solicitacao).exists()


def test_cpf_no_parametro_e_recusado(ana):
    saida = _saida(objetivo="posicao-cliente", parametros=[{"codigo": "cliente-especifico", "valor": "123.456.789-09"}])
    r = _msg(ana, "posição do cliente", provedor=FakeProvider([saida]))
    assert any("CPF" in e for e in r.erros)


# --- Falhas do modelo ------------------------------------------------------------------------

def test_falha_do_modelo_tenta_de_novo_e_desiste(ana):
    p = FakeProvider([FalhaModelo("rede"), FalhaModelo("rede")])
    r = _msg(ana, INADIMPLENTES, provedor=p)
    assert r.tipo == "falha_modelo" and p.chamadas == 2
    assert not RequestContext.objects.exists()
    assert OperationAudit.objects.filter(acao="modelo_falhou").exists()


def test_segunda_tentativa_recupera(ana):
    p = FakeProvider([{"lixo": 1}, _saida()])
    assert _msg(ana, INADIMPLENTES, provedor=p).tipo == "pergunta"


@pytest.mark.parametrize("saida", [
    _saida(objetivo="apagar-tudo"),
    _saida(intencao="executar_sql"),
    _saida(parametros=[{"codigo": "senha", "valor": "x"}]),
    _saida(parametros=[{"codigo": "data-corte", "valor": "x" * 500}]),
    {**_saida(), "sql": "SELECT 1"},
])
def test_saida_invalida_e_falha(ana, saida):
    r = _msg(ana, INADIMPLENTES, provedor=FakeProvider([saida, saida]))
    assert r.tipo == "falha_modelo"


# --- Bloqueios ------------------------------------------------------------------------------

def test_sem_dominio_nao_chama_o_modelo(novo_usuario, politica):
    from iativa.accounts.models import Department

    vendas = Department.objects.create(codigo="vendas", nome="Vendas")
    user = novo_usuario("bia", vendas)
    politica(user, vendas, consultar=True)
    p = FakeProvider()
    r = _msg(user, INADIMPLENTES, provedor=p)
    assert r.tipo == "sem_acesso" and p.chamadas == 0


def test_definicao_ausente_bloqueia(ana):
    ParameterDefinition.objects.filter(codigo="data-corte").update(ativo=False)
    r = _msg(ana, INADIMPLENTES)
    assert r.tipo == "bloqueada" and r.solicitacao.estado == E.BLOQUEADA


def test_metadados_indisponiveis_bloqueia(ana, monkeypatch):
    def falha(*a, **k):
        raise ConnectionError("rede")

    monkeypatch.setattr(orq.descoberta, "entidades", falha)
    r = _msg(ana, INADIMPLENTES)
    assert r.tipo == "bloqueada" and "estrutura do ERP" in r.mensagem


def test_tabela_restrita_bloqueia(ana):
    from iativa.metadata.models import RestricaoMetadado

    RestricaoMetadado.objects.create(tabela="SE5")
    r = _msg(ana, INADIMPLENTES)
    assert r.tipo == "bloqueada"
    assert OperationAudit.objects.filter(acao="acesso_negado", estado_novo="sem_acesso_entidade").exists()


# --- Conversão e esquema --------------------------------------------------------------------

@pytest.mark.parametrize("tipo,texto,esperado", [
    (TipoValor.DATA, "31/12/2025", "2025-12-31"),
    (TipoValor.PERIODO, "01/01/2026..2026-01-31", {"inicio": "2026-01-01", "fim": "2026-01-31"}),
    (TipoValor.NUMERO, "5", 5),
    (TipoValor.NUMERO, "2,5", 2.5),
    (TipoValor.BOOLEANO, "não", False),
])
def test_conversao(tipo, texto, esperado):
    assert converter(SimpleNamespace(tipo=tipo), texto) == esperado


def test_esquema_e_estrito():
    esquema = esquema_json(["a"], ["p"])
    assert esquema["additionalProperties"] is False
    assert esquema["properties"]["objetivo"]["enum"] == ["", "a"]
    with pytest.raises(SaidaInvalida):
        validar({**_saida(objetivo="a"), "extra": 1}, ["a"], ["p"])


# --- Provedor Anthropic (sem rede) ----------------------------------------------------------

class _Cliente:
    def __init__(self, resposta):
        self.resposta, self.chamada = resposta, None
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.chamada = kwargs
        return self.resposta


def _resposta(texto, stop="end_turn"):
    return SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type="text", text=texto)])


def test_anthropic_exige_liberacao_do_dpo(settings):
    settings.IATIVA_AGENTE = {**settings.IATIVA_AGENTE, "provedor": "anthropic", "envio_liberado": False}
    with pytest.raises(ImproperlyConfigured):
        AnthropicProvider()


def test_anthropic_usa_saida_estruturada_sem_dados(settings, ana):
    import json

    settings.IATIVA_AGENTE = {**settings.IATIVA_AGENTE, "envio_liberado": True}
    cliente = _Cliente(_resposta(json.dumps(_saida())))
    r = _msg(ana, INADIMPLENTES, provedor=AnthropicProvider(cliente=cliente))
    assert r.tipo == "pergunta"
    c = cliente.chamada
    assert c["model"] == "claude-opus-5-5" and c["output_config"]["format"]["type"] == "json_schema"
    assert c["fallbacks"] == "default"
    enviado = json.dumps(c, default=str, ensure_ascii=False)
    assert "CLIENTE SINTETICO" not in enviado  # nenhuma linha do ERP vai ao modelo


@pytest.mark.parametrize("resposta", [_resposta("{}", stop="refusal"), _resposta("{", stop="end_turn"),
                                      _resposta("{}", stop="max_tokens")])
def test_anthropic_falhas_viram_falha_modelo(settings, resposta):
    from iativa.agent.esquema import EntradaModelo

    settings.IATIVA_AGENTE = {**settings.IATIVA_AGENTE, "envio_liberado": True}
    with pytest.raises(FalhaModelo):
        AnthropicProvider(cliente=_Cliente(resposta)).interpretar(EntradaModelo("x", "2026-01-01", [], None))
