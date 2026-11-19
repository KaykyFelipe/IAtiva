"""M117: "no mesmo ano" não é verificação de vínculo; busca em lote aceita nome completo acentuado; verificação
sem ninguém verificado não é entrega."""
import inspect

from iativa.agent import investigacao, objetivos as coord, tarefas
from iativa.query import executor


def test_mesmo_ano_nao_vira_verificacao_de_vinculos():
    texto = "Quantos funcionários foram admitidos e desligados no mesmo ano, por filial, em 2026?"
    assert coord.exigencia(texto)[0] != coord.VERIFICACAO
    assert coord.exigencia("Esses funcionários demitidos foram readmitidos com matrícula nova?")[0] == coord.VERIFICACAO


def test_nome_completo_acentuado_passa_na_busca_em_lote():
    assert investigacao._literal_ok("ISABELA SOARES CONCEIÇÃO", 70)
    assert not investigacao._literal_ok("X'; DROP TABLE SRA010 --", 70)
    assert executor.LITERAL_SEGURO.match("JOSÉ DA SILVA") and not executor.LITERAL_SEGURO.match("A';--")


def test_verificacao_sem_ninguem_verificado_falha_para_ir_a_correcao():
    fonte = inspect.getsource(tarefas)
    assert 'if cob["total"] and not cob["verificados"]:' in fonte and "raise NadaVerificado" in fonte
    assert not issubclass(tarefas.NadaVerificado, investigacao.LimiteAtingido)
