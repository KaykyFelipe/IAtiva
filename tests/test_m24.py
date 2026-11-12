"""M24: cálculo já na primeira consulta criada, consolidado com regras da memória, prazo total
efetivo e "quais são os nomes?" depois de uma contagem."""
from io import StringIO

import pytest
from django.core.management import call_command
from django.http import QueryDict

from iativa.accounts.models import Department
from iativa.agent import agente, aprendizado, prazo
from iativa.agent.models import ConsultaModelo, NotaAgente
from iativa.agent.provedores import FakeProvider
from iativa.chat import conversas
from iativa.chat.models import RequestContext
from iativa.memory import servicos
from iativa.memory.models import Escopo, ParameterDefinition
from iativa.query.consolidado import Consolidado

pytestmark = pytest.mark.django_db


@pytest.fixture
def base(erp_sintetico, settings, novo_usuario, politica):
    settings.IATIVA_METADADOS = {"fonte": "sintetico"}
    settings.IATIVA_APRENDIZADO_AUTOAPROVACAO = True
    call_command("seed_exemplos", stdout=StringIO())
    cob = Department.objects.get(codigo="financeiro-cobranca")
    dire = Department.objects.get(codigo="diretoria")
    ana = novo_usuario("ana", cob)
    politica(ana, cob, consultar=True, filiais=["01", "02"])
    return {"ana": ana, "cob": cob, "dire": dire}


def _d(**kw):
    padrao = {"resposta": "", "acao": "conversar", "objetivo": "", "parametros": [], "alvo": "", "nota": "",
              "confirmado": False, "opcoes": []}
    return {**padrao, **kw}


def test_cria_a_primeira_consulta_ja_com_soma(base):
    ana = base["ana"]
    assert not ConsultaModelo.objects.exists()
    soma = {"codigo": "agregacao", "valor": "soma:E1_VALOR"}
    filial = {"codigo": "filial-analise", "valor": "01"}
    p = FakeProvider([
        _d(acao="aprender_tabela", alvo="SE1 titulos", parametros=[filial, soma]),
        _d(acao="criar_consulta", parametros=[filial, soma],
           consulta={"tabela": "SE1", "nome": "Titulos", "colunas": ["E1_NUM", "E1_VALOR"]}),
    ])
    msg, res = conversas.enviar(ana, conversas.nova_individual(ana), "some os títulos da filial 01", p)
    assert res is not None and res.ok, msg.texto
    assert res.colunas == ["Soma de Vlr.Título"] and res.totais.medidas[0].funcao == "SUM"
    assert "candidatos" not in msg.ficha


def test_calculo_em_consulta_fixa_avisa_em_vez_de_listar(base):
    ana = base["ana"]
    p = FakeProvider([_d(acao="preparar_consulta", objetivo="titulos-a-vencer", parametros=[
        {"codigo": "filial-analise", "valor": "01"}, {"codigo": "agregacao", "valor": "soma:E1_VALOR"}])])
    msg, res = conversas.enviar(ana, conversas.nova_individual(ana), "some", p)
    assert res is None and "não faz cálculos" in msg.texto


def _consulta(user, setores, colunas=("E1_NUM", "E1_TIPO", "E1_CLIENTE", "E1_VALOR")):
    q = QueryDict(mutable=True)
    q.update({"nome": "Titulos", "campo_data": ""})
    q.setlist("colunas", list(colunas))
    q.setlist("setores", [str(s.pk) for s in setores])
    return aprendizado.aprovar(user, aprendizado.salvar_proposta(user, "SE1", q))


def test_mestre_nao_soma_setores_com_regras_diferentes(base, novo_usuario, politica):
    cob, dire = base["cob"], base["dire"]
    u = novo_usuario("diretor", dire)
    politica(u, cob, consultar=True, aprovar_conhecimento=True, administrar=True, filiais=["01"])
    politica(u, dire, consultar=True, aprovar_conhecimento=True, administrar=True, filiais=["02"])
    filial = ParameterDefinition.objects.get(codigo="filial-analise")
    for setor, cod in ((cob, "01"), (dire, "02")):
        servicos.aprovar(u, servicos.registrar_valor(u, filial, Escopo.SETOR, cod, setor=setor))
    cm = _consulta(u, [cob, dire])

    def pedir():
        p = FakeProvider([_d(acao="preparar_consulta", objetivo=cm.codigo,
                             parametros=[{"codigo": "agregacao", "valor": "soma:E1_VALOR"}])])
        return conversas.enviar(u, conversas.mestre(u), "some os títulos", p)[1]

    res = pedir()
    assert isinstance(res, Consolidado) and res.somado  # mesmas regras (nenhuma): soma

    for setor, valor in ((cob, "NCC"), (dire, "RA")):
        NotaAgente.objects.create(texto=f"títulos sem {valor}", escopo="setor", setor=setor, tipo="regra",
                                  situacao="aprovada", criado_por=u, aprovado_por=u,
                                  criterios={"tabela": "SE1", "campo": "E1_TIPO", "op": "<>", "valor": valor})
    res = pedir()
    assert not res.somado and "regras da memória" in res.resumo and "mesmas premissas" not in res.resumo


def test_prazo_total_impede_acao_depois_do_modelo_lento(base, settings, monkeypatch):
    settings.IATIVA_AGENTE_CICLO = {"passos": 3, "retentativas": 1, "tempo_s": 1}
    ana = base["ana"]
    cm = _consulta(ana if aprendizado.pode_aprender(ana) else _admin(base), [base["cob"]])
    relogio = {"t": 1000.0}
    monkeypatch.setattr(prazo, "agora", lambda: relogio["t"])

    class Lento(FakeProvider):
        def decidir(self, entrada):
            relogio["t"] += 200  # o modelo demorou 200 s
            return super().decidir(entrada)

    p = Lento([_d(acao="preparar_consulta", objetivo=cm.codigo, parametros=[{"codigo": "filial-analise", "valor": "01"}])])
    msg, res = conversas.enviar(ana, conversas.nova_individual(ana), "títulos", p)
    assert res is None and agente.SEM_TEMPO in msg.texto and "Nada foi consultado" in msg.texto
    assert not RequestContext.objects.exists()


def test_timeout_da_consulta_usa_o_que_resta(monkeypatch):
    relogio = {"t": 0.0}
    monkeypatch.setattr(prazo, "agora", lambda: relogio["t"])
    assert prazo.limitar(30) == 30  # fora de uma mensagem
    with prazo.definir(10):
        relogio["t"] = 7.5
        assert prazo.limitar(30) == 2
        relogio["t"] = 11
        with pytest.raises(prazo.PrazoEsgotado):
            prazo.limitar(30)


def _admin(base):
    from django.contrib.auth import get_user_model

    from iativa.accounts.models import AccessPolicy, UserProfile

    u = get_user_model().objects.create_user("adm", password="senha-de-teste-123")
    UserProfile.objects.create(user=u, department=base["cob"], funcao="colaborador")
    AccessPolicy.objects.create(user=u, department=base["cob"], filiais=["01", "02"], pode_consultar=True,
                                pode_administrar=True, pode_aprovar_conhecimento=True)
    return u


def test_nomes_depois_de_contar_volta_a_listar(base):
    ana = base["ana"]
    cm = _consulta(_admin(base), [base["cob"]], colunas=("E1_NUM", "E1_CLIENTE", "E1_VALOR"))
    c = conversas.nova_individual(ana)
    contar = FakeProvider([_d(acao="preparar_consulta", objetivo=cm.codigo, parametros=[
        {"codigo": "filial-analise", "valor": "TODAS"}, {"codigo": "agregacao", "valor": "contagem:*"}])])
    _, res = conversas.enviar(ana, c, "quantos títulos?", contar)
    assert res.colunas == ["Filial", "Quantidade"]
    # "quais são? me diga os clientes": o modelo repete o objetivo sem cálculo; a filial TODAS é herdada.
    listar = FakeProvider([_d(acao="preparar_consulta", objetivo=cm.codigo)])
    msg, res = conversas.enviar(ana, c, "quais são? me diga os clientes", listar)
    assert res is not None and res.ok, msg.texto
    assert res.colunas == ["Filial", "No. Título", "Cliente", "Vlr.Título"] and len(res.linhas) > 2
