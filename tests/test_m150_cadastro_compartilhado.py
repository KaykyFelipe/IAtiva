"""M150: cadastro compartilhado (DA3 Veículos) não repete o total em cada filial nem vira número solto.

"Quantos veículos tem a empresa?" rodava uma consulta por filial sobre a DA3 (DA3_FILIAL em branco): cada filial
devolvia os mesmos 417 e a resposta mostrava 417 em todas, ou só a soma 3336 (formato "direto" sem a pessoa pedir).
"""
from types import SimpleNamespace

import pytest

from iativa.accounts.models import Department
from iativa.agent.objetivos import Acompanhamento, pede_formato_direto
from iativa.metadata.models import RestricaoMetadado
from iativa.metadata.tipos import Campo as CampoDic, Tabela
from iativa.query.compilador import compilar
from iativa.query.plano import Campo, Coluna, Filtro, Plano
from iativa.query.servico import (SEM_FILIAL_REGISTRO, _dividir_por_filial_do_registro, _rotulo_filial_registro,
                                  campos_filial_do_registro)

FILIAIS = ["001001", "001002"]


def _c(nome, titulo, tamanho=6, tipo="C", descricao=""):
    return CampoDic(nome, tipo, tamanho, 0, titulo, descricao, False, True, "ok")


DA3 = Tabela("DA3", "DA3010", "Veiculos", "compartilhado", True, "DA3_FILIAL", ("DA3_FILIAL", "DA3_COD"), (
    _c("DA3_FILIAL", "Filial"), _c("DA3_COD", "Veiculo", 8), _c("DA3_PLACA", "Placa", 8),
    _c("DA3_FILBAS", "Fil. Base"), _c("DA3_FILATU", "Fil.Atual"), _c("DA3_FILVGA", "Fil.Vge.Atu."),
    _c("DA3_FILTRO", "Filtro", 1),
))


class _Cat:
    def tabelas(self, aliases, com_campos=False):
        return {"DA3": DA3}


def _contagem():
    e = Campo("e1", "DA3_FILIAL")
    return Plano("aprendido-veiculos", base="DA3", filtros=[Filtro(e, "=", "001001")],
                 colunas=[Coluna("Quantidade", Campo("e1", "DA3_COD"), "COUNT", "inteiro")],
                 premissas=["Veículos cadastrados."])


def test_campos_de_filial_do_registro_base_antes_da_atual():
    """M152: a filial base é a dona do veículo; a atual muda com a viagem."""
    assert [c.nome for c in campos_filial_do_registro(DA3)] == ["DA3_FILBAS", "DA3_FILATU", "DA3_FILVGA"]


@pytest.mark.django_db
def test_contagem_em_todas_as_filiais_divide_pela_filial_base():
    setor = Department.objects.create(codigo="ti-x", nome="TI X")
    plano, filial = _dividir_por_filial_do_registro(_contagem(), DA3, FILIAIS, setor.pk)
    assert filial[0] == "DA3_FILBAS" and "Fil.Atual (DA3_FILATU)" in filial[1]
    assert plano.agrupar and plano.colunas[0].rotulo == "Filial" and plano.colunas[0].campo.nome == "DA3_FILBAS"
    sql = compilar(plano, _Cat(), 100)
    assert "GROUP BY e1.DA3_FILBAS" in sql.sql and "e1.DA3_FILBAS IN (?, ?, ?)" in sql.sql
    assert sql.params[-1] == " " * 6  # registros sem filial informada também entram
    assert _contagem().colunas[0].campo.nome == "DA3_COD"  # o plano original não é alterado


@pytest.mark.django_db
def test_campo_oculto_ao_setor_usa_o_proximo():
    setor = Department.objects.create(codigo="ti-y", nome="TI Y")
    RestricaoMetadado.objects.create(tabela="DA3", campo="DA3_FILBAS", ativo=True)
    _, filial = _dividir_por_filial_do_registro(_contagem(), DA3, FILIAIS, setor.pk)
    assert filial[0] == "DA3_FILATU"


@pytest.mark.django_db
def test_pedido_ja_agrupado_ou_filtrado_pela_filial_do_registro_nao_muda():
    setor = Department.objects.create(codigo="ti-z", nome="TI Z")
    agrupado = _contagem()
    agrupado.colunas = [Coluna("Placa", Campo("e1", "DA3_PLACA")), *agrupado.colunas]
    agrupado.agrupar = True
    assert _dividir_por_filial_do_registro(agrupado, DA3, FILIAIS, setor.pk)[1] is None
    filtrado = _contagem()
    filtrado.filtros.append(Filtro(Campo("e1", "DA3_FILATU"), "=", "001002"))
    assert _dividir_por_filial_do_registro(filtrado, DA3, FILIAIS, setor.pk)[1] is None


def test_rotulo_da_filial_do_registro():
    assert _rotulo_filial_registro("      ") == SEM_FILIAL_REGISTRO
    assert _rotulo_filial_registro("001001").startswith("001001")


@pytest.mark.parametrize("texto,direto", [
    ("quantos veiculos tem a empresa?", False),
    ("quantos clientes temos por filial?", False),
    ("quantos veiculos tem a empresa? diga apenas o valor", True),
    ("só o número de veículos", True),
    ("me mostre sem tabela", True),
])
def test_formato_direto_so_quando_pedido(texto, direto):
    assert pede_formato_direto(texto) is direto
    ac = Acompanhamento(texto)
    ac.definir(SimpleNamespace(formato="direto"))
    assert (ac.formato == "direto") is direto


@pytest.mark.django_db
def test_destaque_com_filial_da_propria_consulta_nao_quebra():
    """M151: a coluna Filial vinda da consulta (DA3_FILATU) era deslocada de novo e dava IndexError no destaque."""
    from iativa.query.servico import _destaque

    setor = Department.objects.create(codigo="ti-w", nome="TI W")
    plano, _ = _dividir_por_filial_do_registro(_contagem(), DA3, FILIAIS, setor.pk)
    comp = compilar(plano, _Cat(), 100)
    linhas = [("001001 · Matriz", 7), ("001002 · Filial", 3)]
    formatadas = [list(map(str, l)) for l in linhas]
    texto = _destaque(plano, comp, ["Filial", "Quantidade"], formatadas, linhas, ["texto", "inteiro"])
    assert "Matriz" in texto and "(7)" in texto and "(3)" in texto


@pytest.mark.django_db
@pytest.mark.parametrize("texto,nova", [
    ("quantos veiculos tem a empresa?", True),
    ("qual o total de pedidos de venda em 2026?", True),
    ("some isso", False),
    ("e a média?", False),
    ("qual o total desse resultado por filial?", False),
    ("soma tudo", False),
])
def test_calculo_sobre_resultado_anterior_so_quando_a_mensagem_se_refere_a_ele(setores, novo_usuario, texto, nova):
    """M152: a pergunta repetida somou a tabela antiga (errada) da resposta anterior em vez de consultar de novo."""
    from iativa.agent.agente import pergunta_nova
    from iativa.chat import conversas

    c = conversas.nova_individual(novo_usuario("diego", setores["ti"]))
    assert pergunta_nova(c, texto) is nova


@pytest.mark.django_db
def test_pergunta_curta_repetida_e_nova(setores, novo_usuario):
    from iativa.agent.agente import pergunta_nova
    from iativa.chat import conversas
    from iativa.chat.models import Mensagem, Papel

    c = conversas.nova_individual(novo_usuario("diego", setores["ti"]))
    Mensagem.objects.create(conversa=c, papel=Papel.USUARIO, autor=c.usuario, texto="quantos veículos?")
    assert not pergunta_nova(c, "quantos veiculos?")  # só a mensagem atual (já gravada): não é repetição
    Mensagem.objects.create(conversa=c, papel=Papel.IA, texto="Consulta executada.")
    Mensagem.objects.create(conversa=c, papel=Papel.USUARIO, autor=c.usuario, texto="Quantos veiculos?")
    assert pergunta_nova(c, "quantos veiculos?")
