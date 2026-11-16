"""M55: achados do roteiro do piloto."""
import pytest

from iativa.query.servico import Resultado

from .test_m24 import base  # noqa: F401


def test_so_o_numero_de_um_total_e_o_valor_em_reais():
    r = Resultado(True, "", colunas=["Títulos", "Valor em atraso"], tipos=["inteiro", "dinheiro"],
                  linhas=[["2178", "R$ 5.233.716,97"]])
    assert r.curta == "R$ 5.233.716,97"


@pytest.mark.django_db
def test_preferencia_de_parametro_fica_salva_para_o_usuario(base):
    from iativa.agent.agente import _lembrar_parametros
    from iativa.agent.decisao import validar
    from iativa.memory.models import ParameterBinding, Escopo, ParameterDefinition

    if not ParameterDefinition.objects.filter(codigo="dias-tolerancia-atraso").exists():
        pytest.skip("sem a definição no seed")
    d = validar({"resposta": "", "acao": "lembrar", "objetivo": "", "alvo": "", "nota": "", "confirmado": False,
                 "opcoes": [], "parametros": [{"codigo": "dias-tolerancia-atraso", "valor": "5"}]},
                [], ["dias-tolerancia-atraso"])
    feitas = _lembrar_parametros(base["ana"], d)
    assert feitas and "5" in feitas[0]
    assert ParameterBinding.objects.filter(usuario=base["ana"], escopo=Escopo.USUARIO,
                                           definicao__codigo="dias-tolerancia-atraso").exists()


@pytest.mark.django_db
def test_valor_nao_dito_nao_passa_por_cima_da_preferencia(base):
    from iativa.agent.agente import _lembrar_parametros, _respeitar_preferencias
    from iativa.agent.decisao import validar
    from iativa.memory.models import ParameterDefinition

    if not ParameterDefinition.objects.filter(codigo="dias-tolerancia-atraso").exists():
        pytest.skip("sem a definição no seed")
    base_d = {"resposta": "", "objetivo": "", "alvo": "", "nota": "", "confirmado": False, "opcoes": []}
    _lembrar_parametros(base["ana"], validar({**base_d, "acao": "lembrar", "parametros": [
        {"codigo": "dias-tolerancia-atraso", "valor": "5"}]}, [], ["dias-tolerancia-atraso"]))
    d = validar({**base_d, "acao": "preparar_consulta", "objetivo": "titulos-em-atraso", "parametros": [
        {"codigo": "dias-tolerancia-atraso", "valor": "0"}]}, ["titulos-em-atraso"], ["dias-tolerancia-atraso"])
    assert _respeitar_preferencias(base["ana"], d, "quanto está em atraso?").parametros == ()
    assert _respeitar_preferencias(base["ana"], d, "atraso com tolerância 0").parametros == d.parametros
