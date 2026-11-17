"""M75: contagem com detalhe é regra geral (qualquer consulta), não atalho de admissões."""
from types import SimpleNamespace

from iativa.agent import contagem


def _cm(codigo, campo_data, rotulo):
    return SimpleNamespace(codigo=codigo, spec={'campo_data': campo_data, 'colunas': [
        {'campo': 'X_NOME', 'rotulo': 'Nome', 'tipo': 'texto'},
        {'campo': campo_data, 'rotulo': rotulo, 'tipo': 'data'}]})


def test_evento_escolhe_a_consulta_pelo_campo_de_data_em_qualquer_tabela():
    adm, dem = _cm('adm', 'RA_ADMISSA', 'Data Admis.'), _cm('dem', 'RA_DEMISSA', 'Dt. Demissao')
    notas = _cm('nf', 'F2_EMISSAO', 'Emissão')
    assert contagem.consulta_do_evento('Quantos foram demitidos em agosto?', [adm, dem]) is dem
    assert contagem.consulta_do_evento('Quantos foram admitidos em agosto?', [dem, adm]) is adm
    assert contagem.consulta_do_evento('Quantas notas tiveram emissão em agosto?', [adm, notas]) is notas


def test_lista_so_quando_pedida():
    assert contagem.quer_detalhe(None, 'Quantos clientes novos em agosto? Quais são?')
    assert not contagem.quer_detalhe(None, 'Quantos clientes novos em agosto?')
    assert not contagem.quer_detalhe(None, 'Quantos clientes novos em agosto? Liste. Só o número.')
    assert not contagem.solicitado('Quantos clientes por filial?')


def test_cada_contagem_cumprida_pela_consulta_do_seu_evento(monkeypatch):
    contrato = [{'descricao': 'Contar admissões', 'contagem_detalhada': True},
                {'descricao': 'Contar demissões', 'contagem_detalhada': True},
                {'descricao': 'Mostrar os registros', 'detalhe_contagem': True}]
    monkeypatch.setattr(contagem, '_consulta', lambda res: _cm('adm', 'RA_ADMISSA', 'Data de admissão'))
    assert contagem.entregas_atendidas(contrato, object()) == [0, 2]
