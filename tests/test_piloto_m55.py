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
