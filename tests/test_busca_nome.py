"""Busca por nome opcional ("a Alcans lançou conta?") em consultas fixas e aprendidas."""
from io import StringIO

import pytest
from django.core.management import call_command
from django.http import QueryDict

from iativa.accounts.models import Department
from iativa.agent import aprendizado
from iativa.agent.contratos import OBJETIVOS, campo_busca
from iativa.chat import conversas
from iativa.chat.models import EstadoSolicitacao as E, RequestContext
from iativa.query.plano import padrao_busca

pytestmark = pytest.mark.django_db
ATRASO = "inadimplentes corte 2026-01-01, 0 dias, considerar saldo, filial 01"


@pytest.fixture(autouse=True)
def _base(erp_sintetico, settings):
    settings.IATIVA_METADADOS = {"fonte": "sintetico"}
    settings.IATIVA_APRENDIZADO_AUTOAPROVACAO = True
    call_command("seed_exemplos", stdout=StringIO())


@pytest.fixture
def admin(novo_usuario, politica):
    cob = Department.objects.get(codigo="financeiro-cobranca")
    u = novo_usuario("diego", cob)
    politica(u, cob, consultar=True, administrar=True, aprovar_conhecimento=True, filiais=["01"])
    politica(u, None, aprovar_conhecimento=True, administrar=True)
    return u


@pytest.mark.parametrize("texto,esperado", [
    ("Alcans", "%ALCANS%"),
    ("Alcans Ltda.", "%ALCANS%LTDA%"),
    ("  São João %_ ", "%SAO%JOAO%"),
    ("%%", ""),
])
def test_padrao_busca(texto, esperado):
    assert padrao_busca(texto) == esperado


def test_busca_e_opcional_e_nunca_perguntada(admin):
    assert "texto-busca" in OBJETIVOS["titulos-em-atraso"].opcionais
    c = conversas.nova_individual(admin)
    _, res = conversas.enviar(admin, c, ATRASO)
    assert res is not None and res.ok and len(res.linhas) > 1


def test_busca_filtra_consulta_fixa(admin):
    c = conversas.nova_individual(admin)
    _, todos = conversas.enviar(admin, c, ATRASO)
    msg, res = conversas.enviar(admin, c, ATRASO + ", cliente sintetico 001")
    assert res.ok and 0 < len(res.linhas) < len(todos.linhas)
    assert all("SINTETICO 001" in linha[2][0] for linha in res.linhas_tela)
    assert any("Busca por nome" in p for p in res.premissas)
    assert any(c["nome"] == "Busca por nome" for c in msg.ficha["criterios"])


def test_busca_sem_resultado_nao_quebra(admin):
    c = conversas.nova_individual(admin)
    _, res = conversas.enviar(admin, c, ATRASO + ", empresa Alcans")
    assert res.ok and res.linhas == []
    assert RequestContext.objects.filter(estado=E.CONCLUIDA).count() == 1


def _modelo_sa1(admin, **extra):
    q = QueryDict(mutable=True)
    q.update({"nome": "Clientes cadastrados", **extra})
    q.setlist("colunas", ["A1_COD", "A1_NOME"])
    q.setlist("setores", [str(Department.objects.get(codigo="financeiro-cobranca").pk)])
    return aprendizado.aprovar(admin, aprendizado.salvar_proposta(admin, "SA1", q))


def test_consulta_aprendida_ganha_busca_automatica(admin):
    cm = _modelo_sa1(admin)
    assert campo_busca(cm.spec) == "A1_NOME" and "texto-busca" in OBJETIVOS[cm.codigo].opcionais
    c = conversas.nova_individual(admin)
    p = {"resposta": "", "acao": "preparar_consulta", "objetivo": cm.codigo, "alvo": "", "nota": "",
         "confirmado": False, "opcoes": [],
         "parametros": [{"codigo": "filial-analise", "valor": "01"}, {"codigo": "texto-busca", "valor": "Sintético 002"}]}
    from iativa.agent.provedores import FakeProvider

    _, res = conversas.enviar(admin, c, "o cliente sintético 002 está cadastrado?", FakeProvider([p]))
    assert res.ok and res.linhas and all("SINTETICO 002" in " ".join(linha) for linha in res.linhas)


def test_campo_busca_invalido_e_recusado(admin):
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError, match="busca por nome"):
        _modelo_sa1(admin, campo_busca="A1_LOJA")


def _decisao(**kw):
    base = {"resposta": "", "acao": "conversar", "objetivo": "", "parametros": [], "alvo": "", "nota": "",
            "confirmado": False, "opcoes": []}
    return {**base, **kw}


def test_continuacao_herda_criterios_e_executa(admin):
    from iativa.agent.provedores import FakeProvider

    c = conversas.nova_individual(admin)
    _, primeira = conversas.enviar(admin, c, ATRASO)
    # "e do cliente sintetico 001?": o modelo manda só a busca, sem os outros critérios.
    p = FakeProvider([_decisao(acao="preparar_consulta", objetivo="titulos-em-atraso",
                               parametros=[{"codigo": "texto-busca", "valor": "sintetico 001"}])])
    _, res = conversas.enviar(admin, c, "e do cliente sintetico 001?", p)
    assert res is not None and res.ok and 0 < len(res.linhas) < len(primeira.linhas)


def test_executar_sem_consulta_aberta_roda_de_novo(admin):
    from iativa.agent.provedores import FakeProvider

    c = conversas.nova_individual(admin)
    conversas.enviar(admin, c, ATRASO)
    # Caso real: o Gemini respondeu "Processando agora" com executar_consulta depois da anterior concluída.
    p = FakeProvider([_decisao(acao="executar_consulta", resposta="Processando agora.",
                               parametros=[{"codigo": "texto-busca", "valor": "sintetico 002"}])])
    msg, res = conversas.enviar(admin, c, "procure se tem do sintetico 002", p)
    assert res is not None and res.ok and "Consulta executada" in msg.texto
    assert RequestContext.objects.filter(estado=E.CONCLUIDA).count() == 2
