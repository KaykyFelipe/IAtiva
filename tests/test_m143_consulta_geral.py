"""M143: motor de consulta geral — junções SX9 em 2 saltos, agrupamento multi-dimensão, preservação de "por filial",
filtro dinâmico em tabelas ligadas/diferente, lotes curtos de nomes (VPN/MTU), cache de catálogo e prazo na revisão."""
from io import StringIO

import pytest
from django.core.management import call_command

from iativa.accounts.models import Department
from iativa.agent import prazo, revisao
from iativa.agent.models import ConsultaModelo
from iativa.agent.provedores import FakeProvider
from iativa.chat import conversas
from iativa.metadata.catalogo import abrir_catalogo
from iativa.query import nomes
from iativa.query.servico import Resultado

pytestmark = pytest.mark.django_db


def _d(**kw):
    base = {"resposta": "", "acao": "conversar", "objetivo": "", "parametros": [], "alvo": "", "nota": "",
            "confirmado": False, "opcoes": []}
    return {**base, **kw}


@pytest.fixture
def ana(erp_sintetico, settings, novo_usuario, politica):
    settings.IATIVA_METADADOS = {"fonte": "sintetico"}
    call_command("seed_exemplos", stdout=StringIO())
    cob = Department.objects.get(codigo="financeiro-cobranca")
    ti = Department.objects.get(codigo="ti")
    u = novo_usuario("ana", cob)
    politica(u, cob, consultar=True, filiais=["01", "02"])
    politica(u, ti, administrar=True)
    return u


def _criar_se5(ana):
    c = conversas.nova_individual(ana)
    conversas.enviar(ana, c, "movimentos", FakeProvider([
        _d(acao="aprender_tabela", alvo="SE5"),
        _d(acao="criar_consulta", parametros=[{"codigo": "filial-analise", "valor": "01"}],
           consulta={"tabela": "SE5", "nome": "Movimentos SE5",
                     "colunas": ["E5_NUMERO", "E5_DATA", "E5_VALOR", "E5_TIPODOC"],
                     "campo_data": "E5_DATA"}),
    ]))
    return ConsultaModelo.objects.get(nome="Movimentos SE5")


def test_juncao_encadeada_dois_saltos_agrupa_e_filtra_sozinha(ana):
    """SE5 -> SE1 -> SA1 (2 saltos no SX9): agrupa e filtra por campo do SA1 sem código específico."""
    cm = _criar_se5(ana)
    c = conversas.nova_individual(ana)
    p = FakeProvider([_d(
        acao="preparar_consulta", objetivo=cm.codigo, entrega="calculo",
        parametros=[
            {"codigo": "filial-analise", "valor": "TODAS"},
            {"codigo": "periodo-analise", "valor": "01/01/2020..31/12/2030"},
            {"codigo": "filtro-campo", "valor": "E5_SITUACA!=C; A1_XSEGMEN!=ZZZ"},
            {"codigo": "agregacao", "valor": "soma:E5_VALOR por A1_XSEGMEN, E1_CLIENTE por filial"},
        ],
    )])
    # Inclui E5_SITUACA nas colunas base para exercitar != junto do campo de 2 saltos A1_XSEGMEN.
    cm.spec = {**cm.spec, "colunas": cm.spec["colunas"] + [{"campo": "E5_SITUACA", "rotulo": "Situação", "tipo": "texto"}]}
    cm.save(update_fields=["spec"])

    msg, res = conversas.enviar(ana, c, "Quanto entrou por segmento e cliente, por filial?", p)
    assert res is not None and res.ok, msg.texto
    assert res.colunas[:3] == ["Filial", "Segmento", "Cliente"], res.colunas
    assert "Nome (Cliente)" not in res.colunas or "Soma de Valor" in res.colunas  # SA1 já juntada na consulta
    cm.refresh_from_db()
    tabelas_j = [j["tabela"] for j in cm.spec["juncoes"]]
    assert "SE1" in tabelas_j and "SA1" in tabelas_j
    sa1_j = next(j for j in cm.spec["juncoes"] if j["tabela"] == "SA1")
    se1_j = next(j for j in cm.spec["juncoes"] if j["tabela"] == "SE1")
    assert sa1_j.get("de") == se1_j["alias"]


def test_por_filial_implicito_quando_modelo_esquece_no_agrupamento(ana):
    """Pergunta explícita 'por filial' + modelo mandando só 'soma:CAMPO por GRUPO': mantém separado por filial e traz nome."""
    cm = _criar_se5(ana)
    c = conversas.nova_individual(ana)
    p = FakeProvider([_d(
        acao="preparar_consulta", objetivo=cm.codigo, entrega="calculo",
        parametros=[
            {"codigo": "filial-analise", "valor": "TODAS"},
            {"codigo": "periodo-analise", "valor": "01/01/2020..31/12/2030"},
            {"codigo": "agregacao", "valor": "soma:E5_VALOR por E1_CLIENTE"},
        ],
    )])
    msg, res = conversas.enviar(ana, c, "Quanto cada cliente movimentou em outubro, por filial?", p)
    assert res is not None and res.ok, msg.texto
    assert res.colunas[0] == "Filial", res.colunas
    assert "Cliente" in res.colunas and "Nome (Cliente)" in res.colunas, res.colunas
    assert len({l[0] for l in res.linhas}) >= 1


def test_lotes_curtos_na_busca_de_nomes_para_vpn_mtu(ana, monkeypatch):
    assert nomes._LOTE <= 30
    tamanhos = []
    real_executar = nomes.executar

    def espia(cat, sql, params, limite, timeout_s, validar_final=None):
        # Conta quantos códigos vieram no IN (...) descontando o parâmetro de filial.
        tamanhos.append(len(params) - 1)
        return real_executar(cat, sql, params, limite, timeout_s, validar_final=validar_final)

    monkeypatch.setattr(nomes, "executar", espia)
    with abrir_catalogo() as cat:
        mestre = cat.tabelas(["SA1"], com_campos=True)["SA1"]
        chaves = {(f"{i:06d}",) for i in range(1, 76)}
        nomes._buscar(cat, mestre, [("A1_COD", "E1_CLIENTE")], "A1_NOME", chaves, ["01"], 5)
    assert tamanhos == [30, 30, 15]


def test_catalogo_faz_cache_de_metadados_na_mesma_sessao(erp_sintetico, monkeypatch):
    with abrir_catalogo() as cat:
        chamadas = []
        real_colunas = cat.fisico.colunas

        def conta(fisica):
            chamadas.append(fisica)
            return real_colunas(fisica)

        monkeypatch.setattr(cat.fisico, "colunas", conta)
        cat.tabelas(["SE1", "SA1"], com_campos=True)
        cat.tabelas(["SE1", "SA1"], com_campos=True)
        sem = cat.tabelas(["SE1", "SA1"], com_campos=False)
        assert chamadas == ["SE1010", "SA1010"]
        assert sem["SE1"].campos == ()


def test_revisor_preserva_resposta_validada_quando_prazo_esta_no_fim(settings):
    settings.IATIVA_AGENTE_CICLO = {**settings.IATIVA_AGENTE_CICLO, "continuacoes": 0}
    r = Resultado(True, "Total calculado", colunas=["Filial", "Vendedor", "Soma"], tipos=["texto", "texto", "dinheiro"],
                  linhas=[["01", "000001", "R$ 100,00"]])
    saida = type("S", (), {"resultado": r})()

    class ExplodeSeChamar:
        def planejar(self, entrada):
            raise AssertionError("Não deveria chamar o modelo com menos de 10s restantes.")

    with prazo.definir(5.0):
        assert revisao.revisar(None, ExplodeSeChamar(), "Quanto cada vendedor vendeu por filial?", [], saida) == ""
