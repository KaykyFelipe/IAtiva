"""M138: "10 fornecedores com mais pedidos de compra em 2026" parava sem resposta. Com as tabelas em mãos o modelo
pedia "aprender_tabela" de novo, e depois uma resposta fora do formato era repetida às cegas até "sem decisão válida".
Vale para qualquer pergunta de qualquer setor."""
import pytest

from iativa.agent.decisao import DecisaoInvalida, validar
from iativa.agent.models import ConsultaModelo
from iativa.chat import conversas

from .test_calculo_apos_aprender import CRITERIOS, ITENS
from .test_ciclo_agente import Espiao, _d, base  # noqa: F401
from .test_diagnostico import faturamento  # noqa: F401


def test_texto_longo_e_listas_longas_sao_cortados_e_operador_sinonimo_vale():
    d = validar(_d(acao="criar_consulta", resposta="x" * 5000, consulta={
        "tabela": "SC7", "nome": "n" * 300, "colunas": [f"C7_C{i}" for i in range(30)],
        "filtraveis": [f"C7_F{i}" for i in range(15)], "filtros": [{"campo": "C7_ENCER", "operador": "<>", "valor": "E"}],
        "juncoes": [{"tabela": "SA2", "colunas": ["A2_NOME", "A2_COD", "A2_LOJA", "A2_CGC", "A2_EST", "A2_MUN"]}]}),
        [], [])
    assert len(d.consulta["colunas"]) == 20 and len(d.consulta["filtraveis"]) == 10
    assert len(d.consulta["nome"]) == 120 and d.consulta["filtros"][0]["operador"] == "diferente"
    assert len(d.consulta["juncoes"][0]["colunas"]) == 5


def test_continua_estrito_no_que_muda_o_sentido():
    with pytest.raises(DecisaoInvalida):
        validar(_d(acao="criar_consulta", consulta={"tabela": "SC7", "filtros": [
            {"campo": "C7_EMISSAO", "operador": "maior", "valor": "20260101"}]}), [], [])
    with pytest.raises(DecisaoInvalida):
        validar({**_d(), "sql": "SELECT 1"}, [], [])


class Recusa(Espiao):
    """1ª resposta fora do formato; a 2ª só vem certa se o motivo da recusa chegou ao modelo."""

    def decidir(self, entrada):
        self.entradas.append(entrada)
        if any(o.get("situacao") == "decisao_invalida" for o in entrada.observacoes or []):
            return _d(resposta="ok")
        return _d(acao="inventada")


@pytest.mark.django_db
def test_motivo_da_recusa_volta_ao_modelo(base):  # noqa: F811
    p = Recusa([])
    msg, _ = conversas.enviar(base["ana"], conversas.nova_individual(base["ana"]), "oi", p)
    assert any(o.get("situacao") == "decisao_invalida" for e in p.entradas for o in (e.observacoes or []))


class PedeTabelaDeNovo(Espiao):
    """Com tabelas_para_aprender, pede aprender_tabela de novo; só monta a consulta depois da instrução explícita."""

    def decidir(self, entrada):
        self.entradas.append(entrada)
        self.chamadas += 1
        if getattr(entrada, "tabelas_para_aprender", None):
            if any(o.get("situacao") == "etapa_errada" for o in entrada.observacoes or []):
                return _d(acao="criar_consulta", entrega="calculo", consulta=ITENS,
                          parametros=CRITERIOS + [{"codigo": "agregacao", "valor": "soma:D2_TOTAL"}])
        return _d(acao="aprender_tabela", alvo="SD2 itens de nota", entrega="calculo", parametros=CRITERIOS)


@pytest.mark.django_db
def test_com_as_tabelas_em_maos_monta_a_consulta(faturamento):  # noqa: F811
    from iativa.accounts.models import AccessPolicy, Department
    AccessPolicy.objects.create(user=faturamento["ana"], department=Department.objects.get(codigo="ti"),
                                pode_administrar=True)
    c = conversas.nova_individual(faturamento["ana"])
    msg, res = conversas.enviar(faturamento["ana"], c, "some o total dos itens de nota da filial 01 em setembro",
                                PedeTabelaDeNovo([]))
    assert ConsultaModelo.objects.filter(tabela="SD2").exists(), msg.texto
    assert res is not None and res.ok, msg.texto
