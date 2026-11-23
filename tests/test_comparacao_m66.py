"""M66: comparação geral de dois períodos (auditoria 17/11, etapa 2 do prompt de melhorias)."""
import sqlite3

import pytest
from django.http import QueryDict

from iativa.agent import aprendizado, comparacao
from iativa.agent.provedores import FakeProvider
from iativa.chat import conversas
from iativa.manutencao.models import Incidente

from .test_ciclo_agente import base, _d  # noqa: F401
from .test_diagnostico import faturamento  # noqa: F401


def _consulta_sd2(b, colunas):
    q = QueryDict(mutable=True)
    q.update({'nome': 'Itens sintéticos', 'campo_data': 'D2_EMISSAO'})
    q.setlist('colunas', colunas)
    q.setlist('setores', [str(b['cob'].pk)])
    return aprendizado.aprovar(b['admin'], aprendizado.salvar_proposta(b['admin'], 'SD2', q))


def _comparar(b, cm, texto, agregacao=None):
    params = [{'codigo': 'filial-analise', 'valor': '01'}]
    if agregacao:
        params.append({'codigo': 'agregacao', 'valor': agregacao})
    p = FakeProvider([_d(acao='preparar_consulta', objetivo=cm.codigo, entrega='calculo', parametros=params)])
    m, r = conversas.enviar(b['ana'], conversas.nova_individual(b['ana']), texto, p)
    return m, r, p


def test_periodos_gerais_sem_exigir_faturamento():
    assert [x for _, _, x in comparacao.periodos('compare as admissões de julho e agosto de 2026')] == ['07/2026', '08/2026']
    assert [x for _, _, x in comparacao.periodos('variação de 2025 versus 2026')] == ['2025', '2026']
    assert comparacao.periodos('quanto faturamos em julho?') == []
    assert comparacao.periodos('Qual o faturamento e a quantidade de notas de setembro e outubro?') == []
    assert comparacao.periodos('Qual o faturamento de setembro e outubro e liste os desligados?') == []


@pytest.mark.parametrize('agrupamento', ['', ' por filial', 'comparação por filial'])
def test_faturamento_de_dois_meses_entrega_uma_tabela_com_os_meses_do_pedido(faturamento, agrupamento):
    b = faturamento
    with sqlite3.connect(b['erp']) as conn:
        conn.executemany('INSERT INTO SD2010 VALUES (?,?,?,?,?,?,?)', [
            ('01', '20260801', '501', '5102', 9000, 1, ' '),
            ('01', '20261001', '501', '5102', 2500, 1, ' '),
            ('02', '20260912', '501', '5102', 123, 1, ' '),
            ('02', '20261012', '501', '5102', 321, 1, ' '),
        ])
    cm = _consulta_sd2(b, ['D2_EMISSAO', 'D2_TOTAL'])
    comparacao_explicita = agrupamento.startswith('comparação')
    p = FakeProvider([_d(acao='preparar_consulta', objetivo=cm.codigo, entrega='calculo', parametros=[
        {'codigo': 'filial-analise', 'valor': 'TODAS' if agrupamento else '01'},
        {'codigo': 'periodo-analise', 'valor': '01/08/2026..31/08/2026'},
        {'codigo': 'agregacao', 'valor': 'soma:D2_TOTAL' + (' por filial' if agrupamento else '')},
    ])], analises=[{'natureza': 'dados', 'setores': [b['cob'].codigo], 'confianca': 1,
                   'resumo': '', 'memorias': [], 'entregas': [
                       {'descricao': f'Faturamento de {"outubro" if comparacao_explicita else "agosto"} de 2026',
                        'tipo': 'calculo', 'metrica': 'soma'},
                       {'descricao': 'Faturamento de setembro de 2026', 'tipo': 'calculo', 'metrica': 'soma'},
                   ]}])
    texto = ('Compare o faturamento de setembro e outubro de 2026 por filial' if comparacao_explicita
             else 'Qual o faturamento de setembro e outubro de 2026' + agrupamento + '?')
    msg, r = conversas.enviar(b['ana'], conversas.nova_individual(b['ana']), texto, p)
    assert r is not None and r.ok and r.colunas[1:3] == ['09/2026', '10/2026'], msg.texto
    assert r.linhas[0][1:3] == ['R$ 8.431,27', 'R$ 2.500,00']
    if agrupamento:
        assert r.colunas[0] == 'Filial' and len(r.linhas) == 2
        assert r.rodape[:3] == ['Total', 'R$ 8.554,27', 'R$ 2.821,00']
        if comparacao_explicita:
            assert r.rodape[3] == 'R$ -5.733,27'
    else:
        assert not r.rodape
    assert p.chamadas == 1 and not Incidente.objects.exists()
    from iativa.chat.resultados import restaurar
    salvo = restaurar(b['ana'], msg.resultado)
    assert salvo.colunas == r.colunas and salvo.linhas == r.linhas and salvo.rodape == r.rodape
    if agrupamento:
        from django.template.loader import render_to_string
        html = render_to_string('chat/_resultado.html', {'resultado': salvo})
        assert '<tfoot>' in html and 'R$ 8.554,27' in html.split('<tfoot>')[1]


def test_total_por_filial_em_consulta_de_um_mes(faturamento):
    b = faturamento
    cm = _consulta_sd2(b, ['D2_EMISSAO', 'D2_TOTAL'])
    p = FakeProvider([_d(acao='preparar_consulta', objetivo=cm.codigo, entrega='calculo', parametros=[
        {'codigo': 'filial-analise', 'valor': 'TODAS'},
        {'codigo': 'periodo-analise', 'valor': '01/09/2026..30/09/2026'},
        {'codigo': 'agregacao', 'valor': 'soma:D2_TOTAL por filial'},
    ])])
    msg, r = conversas.enviar(b['ana'], conversas.nova_individual(b['ana']),
                             'Qual o faturamento de setembro de 2026 por filial?', p)
    assert r is not None and r.colunas[0] == 'Filial', msg.texto
    assert r.rodape == ['Total', 'R$ 8.431,27']


def test_compara_por_outro_agrupamento_sem_forcar_filial(faturamento):
    """Agrupamento pedido (por CFOP) é preservado; nada vira "por filial"."""
    b = faturamento
    with sqlite3.connect(b['erp']) as conn:
        conn.execute("INSERT INTO SD2010 VALUES ('01','20260808','501','5102',1000,1,' ')")
    cm = _consulta_sd2(b, ['D2_EMISSAO', 'D2_CF', 'D2_TOTAL'])
    m, r, p = _comparar(b, cm, 'Compare o total de agosto e setembro de 2026 por CFOP', 'soma:D2_TOTAL por D2_CF')
    assert r is not None and r.medida_calculada == 'comparacao', m.texto
    assert r.colunas[0] != 'Filial'
    grupos = {l[0]: l for l in r.linhas}
    assert set(grupos) == {'5102', '5915'}
    # 5915 não existe em agosto: soma conta zero e a variação é indefinida (base zero), não inventada.
    assert grupos['5915'][1] == 'R$ 0,00' and grupos['5915'][4] == 'Indefinido (base zero)'
    assert p.chamadas == 1 and not Incidente.objects.exists()


def test_compara_contagem(faturamento):
    b = faturamento
    cm = _consulta_sd2(b, ['D2_EMISSAO', 'D2_TOTAL'])
    m, r, _ = _comparar(b, cm, 'Compare a quantidade de itens de agosto com setembro de 2026', 'contagem:*')
    assert r is not None and r.medida_calculada == 'comparacao', m.texto
    assert r.linhas[0][1:3] == ['0', '3'], r.linhas


def test_media_sem_registros_nao_vira_zero(faturamento):
    b = faturamento
    cm = _consulta_sd2(b, ['D2_EMISSAO', 'D2_TOTAL'])
    m, r, _ = _comparar(b, cm, 'Compare a média de julho e setembro de 2026', 'media:D2_TOTAL')
    assert r is not None, m.texto
    # Julho sem registros: média não existe (não é zero); a variação não é calculável.
    assert r.linhas[0][1] == 'Sem valor' and r.linhas[0][3] == 'Não calculável', r.linhas


def test_por_campo_de_filial_equivale_a_por_filial():
    from iativa.query.plano import ler_agregacao
    spec = {'colunas': [{'campo': 'D2_TOTAL', 'tipo': 'dinheiro'}], 'campo_data': 'D2_EMISSAO'}
    assert ler_agregacao('soma:D2_TOTAL por D2_FILIAL', spec)[1] == ''
    assert ler_agregacao('soma:D2_TOTAL por filial', spec)[1] == ''


def test_agrupamento_por_mes_e_duplo_normalizado():
    from types import SimpleNamespace
    d = SimpleNamespace(parametros=(('agregacao', 'soma:D2_TOTAL por D2_FILIAL por mes'),))
    assert comparacao._agregacao(d, 'compare julho e agosto por filial') == 'soma:D2_TOTAL por D2_FILIAL'
    d = SimpleNamespace(parametros=(('agregacao', 'soma:D2_TOTAL por mes'),))
    assert comparacao._agregacao(d, 'compare julho e agosto') == 'soma:D2_TOTAL'


def test_periodos_so_da_oracao_da_comparacao():
    t = ('compare o faturamento de julho e agosto de 2026 por filial e liste os funcionários admitidos em '
         'agosto de 2026 com nome e função')
    assert [x for _, _, x in comparacao.periodos(t)] == ['07/2026', '08/2026']


def test_lista_do_pedido_misto_nao_e_cumprida_pela_comparacao():
    """M68: a tabela de faturamento por filial não cumpre "liste os admitidos com nome e função"."""
    from iativa.agent import entregas, planejamento
    from iativa.agent.agente import Saida
    from iativa.query.servico import Resultado
    from .test_ciclo_agente import _d as d_
    from iativa.agent.decisao import validar
    t = ('compare o faturamento de julho e agosto de 2026 por filial e liste os funcionários admitidos em '
         'agosto de 2026 com nome e função')
    a = comparacao.planejar(t, entregas.planejar(t, {'entregas': [
        {'descricao': 'Comparar faturamento', 'tipo': 'calculo', 'metrica': 'comparacao'},
        {'descricao': 'Listar os funcionários admitidos em agosto de 2026 com nome e função', 'tipo': 'consulta',
         'metrica': ''}]}))
    lista = a['entregas'][1]
    assert lista['campos_pedidos'] == ['nome', 'funcao']
    fat = Resultado(True, '', colunas=['Filial', 'Valor'], tipos=['texto', 'dinheiro'], linhas=[['01', '1']])
    assert planejamento.conferir({'entregas': [lista]}, validar(d_(), [], []), Saida('', resultado=fat))
    rh = Resultado(True, '', colunas=['Nome', 'Função', 'Admissão'], tipos=['texto'] * 3, linhas=[['x', 'y', 'z']])
    assert not planejamento.conferir({'entregas': [lista]}, validar(d_(), [], []), Saida('', resultado=rh))


def _contrato_misto():
    from iativa.agent import entregas
    t = ('compare o faturamento de julho e agosto de 2026 por filial e liste os funcionários admitidos em '
         'agosto de 2026 com nome e função')
    modelo = {'entregas': [
        {'descricao': 'Faturamento de julho de 2026 por filial (venda com TES que gera duplicata)', 'tipo': 'consulta', 'metrica': 'soma'},
        {'descricao': 'Faturamento de agosto de 2026 por filial (venda com TES que gera duplicata)', 'tipo': 'consulta', 'metrica': 'soma'},
        {'descricao': 'Total de funcionários admitidos em agosto de 2026', 'tipo': 'calculo', 'metrica': 'contagem'},
        {'descricao': 'Lista dos funcionários admitidos em agosto de 2026, com nome completo e função', 'tipo': 'consulta', 'metrica': ''}]}
    return t, comparacao.planejar(t, entregas.planejar(t, modelo))['entregas']


def test_pedido_misto_so_tem_as_entregas_pedidas():
    """M68: períodos separados e a contagem não pedida saem; ficam a comparação e a lista."""
    _, contrato = _contrato_misto()
    assert [e.get('comparacao', False) for e in contrato] == [True, False]
    assert contrato[1]['campos_pedidos'] == ['nome', 'funcao']
    t = 'quantos funcionários foram admitidos em agosto e liste com nome'
    from iativa.agent import entregas
    a = entregas.planejar(t + ' e compare', {'entregas': [{'descricao': 'Total', 'tipo': 'calculo', 'metrica': 'contagem'}]})
    assert a['entregas']  # "quantos" pede a contagem


def test_comparacao_nao_roda_no_passo_da_lista():
    from types import SimpleNamespace
    _, contrato = _contrato_misto()
    ac = SimpleNamespace(contrato=contrato, cumpridas=[])
    assert comparacao.deste_passo(ac, SimpleNamespace(atende=(0,), objetivo='x'))
    assert not comparacao.deste_passo(ac, SimpleNamespace(atende=(1,), objetivo='x'))
    ac.cumpridas = [0]
    assert not comparacao.deste_passo(ac, SimpleNamespace(atende=(), objetivo='x'))


def test_resposta_final_mostra_todas_as_entregas_sem_texto_de_intencao():
    from iativa.agent.agente import Saida, _juntar
    from iativa.query.servico import Resultado
    comp = Resultado(True, 'Comparação', colunas=['Filial', 'Valor'], tipos=['texto', 'dinheiro'], linhas=[['01', '1']])
    comp.titulo_entrega = 'Comparar os dois períodos'
    lista = Resultado(True, 'Lista', colunas=['Nome', 'Função'], tipos=['texto', 'texto'], linhas=[['x', 'y']])
    lista.titulo_entrega = 'Lista dos admitidos'
    previa = Saida('Comparação pronta.\n\nA comparação está pronta. Agora vou buscar a lista dos admitidos.', resultado=comp)
    s = _juntar(previa, Saida('Lista pronta.', resultado=lista))
    assert getattr(s.resultado, 'consolidado', False)
    assert [b.titulo for b in s.resultado.blocos] == ['Comparar os dois períodos', 'Lista dos admitidos']
    assert 'Agora vou' not in s.texto and 'Lista pronta.' in s.texto
    # Passo seguinte que não cumpriu nada não troca a tabela entregue.
    outra = Resultado(True, 'Outra', colunas=['A'], tipos=['texto'], linhas=[['1']])
    s2 = _juntar(Saida('', resultado=lista), Saida('', resultado=outra))
    assert s2.resultado is lista


def test_pedido_misto_cumprido_quando_todas_as_entregas_conferem():
    """M69: comparação (cálculo) + lista (consulta): lista entregue por último fecha o pedido."""
    from iativa.agent.objetivos import Acompanhamento, CALCULO, PERGUNTA
    _, contrato = _contrato_misto()
    ac = Acompanhamento('x', exig=CALCULO, contrato=contrato, cumpridas=[0])
    assert not ac.cumprido('consulta')
    ac.cumpridas.append(1)
    assert ac.cumprido('consulta') and not ac.cumprido(PERGUNTA) and not ac.cumprido('')


RANKING = 'Quais os 2 clientes que mais caíram em faturamento de setembro para outubro, em %?'


def test_resposta_do_ano_preserva_ranking(faturamento):
    from iativa.chat.models import Mensagem

    user = faturamento['ana']
    c = conversas.nova_individual(user)
    Mensagem.objects.create(conversa=c, papel='usuario', texto=RANKING)
    Mensagem.objects.create(conversa=c, papel='ia', texto='De qual ano são setembro e outubro?')
    m = Mensagem.objects.create(conversa=c, papel='usuario', texto='Se eu não informar o ano, então estou me referindo a 2025')
    pedido = comparacao.contextualizar_ano(c, m.texto, m.pk)
    assert comparacao.ranking(pedido) == 2
    assert [r for _, _, r in comparacao.periodos(pedido)] == ['09/2025', '10/2025']


def test_ranking_compara_todos_os_clientes_antes_de_aplicar_top(faturamento, monkeypatch):
    from iativa.agent import agente
    from iativa.query.servico import Resultado

    b = faturamento
    cm = _consulta_sd2(b, ['D2_EMISSAO', 'D2_TOTAL'])
    cm.spec['colunas'].append({'campo': 'D2_CLIENTE', 'rotulo': 'Cliente', 'tipo': 'texto'})
    cm.save(update_fields=['spec'])
    chamadas = []

    def preparar(user, c, d, *_args, **_kwargs):
        params = dict(d.parametros)
        chamadas.append(params)
        primeiro = params['periodo-analise'].startswith('01/09')
        valores = [('A', '100'), ('B', '1000'), ('C', '100'), ('D', '0')] if primeiro else [
            ('A', '10'), ('B', '400'), ('C', '110'), ('D', '10')]
        return agente.Saida('', {}, resultado=Resultado(
            True, '', colunas=['Cliente', 'Total'], tipos=['texto', 'dinheiro'], linhas=valores))

    monkeypatch.setattr(agente, '_preparar', preparar)
    m, r, _ = _comparar(b, cm, RANKING, 'soma:D2_TOTAL por D2_CLIENTE top 2')
    assert r is not None and r.medida_calculada == 'comparacao', m.texto
    assert [linha[0] for linha in r.linhas] == ['A', 'B']
    assert len(chamadas) == 2
    assert all(c['agregacao'] == 'soma:D2_TOTAL por D2_CLIENTE' for c in chamadas)


def test_ranking_incompleto_nao_publica_a_tabela_de_um_mes(faturamento, monkeypatch):
    from iativa.agent import agente
    from iativa.query.servico import Resultado

    b = faturamento
    cm = _consulta_sd2(b, ['D2_EMISSAO', 'D2_TOTAL'])
    monkeypatch.setattr(comparacao, 'executar', lambda *_a, **_kw: None)
    monkeypatch.setattr(agente, '_agir', lambda *_a, **_kw: agente.Saida(
        'Consulta executada.', resultado=Resultado(True, 'Setembro', colunas=['Cliente', 'Total'],
                                                    tipos=['texto', 'dinheiro'], linhas=[['A', '10']])))
    decisao = _d(acao='preparar_consulta', objetivo=cm.codigo, entrega='calculo')
    falta = _d(acao='falta_capacidade', capacidade='Comparar e ordenar a queda de faturamento por cliente')
    p = FakeProvider([decisao, falta, falta])  # tenta descobrir outra fonte antes de abrir manutenção
    _, resultado = conversas.enviar(b['ana'], conversas.nova_individual(b['ana']), RANKING, p)
    assert resultado is None
    assert Incidente.objects.exists()


PEDIDO_EVENTOS = ("Compare setembro e outubro de 2026 por filial. Em cada mês, mostre quantos contratos "
                  "foram assinados e quantas locações começaram.")


def test_assinatura_nao_comprova_inicio_de_locacao(faturamento, monkeypatch):
    from types import SimpleNamespace
    from iativa.query import servico
    from iativa.agent.agente import Saida

    cm = _consulta_sd2(faturamento, ['D2_EMISSAO', 'D2_TOTAL'])
    cm.tabela = 'FP0'
    cm.spec['campo_data'] = 'FP0_ZDTASS'
    cm.save(update_fields=['tabela', 'spec'])
    contrato = comparacao.planejar(PEDIDO_EVENTOS, {'entregas': []})['entregas']
    assert [item['evento_mensal'] for item in contrato] == ['assinatura', 'inicio_locacao']
    monkeypatch.setattr(servico, '_valores', lambda *_: {'periodo-analise': '01/09/2026..31/10/2026',
                                                       'agregacao': 'contagem_distinta:FP0_PROJET por mes'})
    s = SimpleNamespace(objetivo=cm.codigo, usuario=faturamento['ana'])
    resultado = servico.Resultado(True, '', colunas=['Filial', 'Mês', 'Quantidade'], situacao='sucesso')
    saida = Saida('', solicitacao=s, resultado=resultado)
    assert comparacao.comprova_evento_mensal(contrato[0], saida)
    assert not comparacao.comprova_evento_mensal(contrato[1], saida)
    cm.tabela = 'FPA'
    cm.spec['campo_data'] = 'FPA_DTINI'
    cm.save(update_fields=['tabela', 'spec'])
    assert not comparacao.comprova_evento_mensal(contrato[0], saida)
    assert comparacao.comprova_evento_mensal(contrato[1], saida)


@pytest.mark.parametrize("causa", ["acao_repetida", "prazo"])
def test_falha_de_calculo_no_worker_preserva_pedido_para_manutencao(faturamento, settings, causa):
    from iativa.agent import agente, objetivos
    from iativa.agent.models import Objetivo, SituacaoObjetivo
    from iativa.chat.models import Mensagem
    from iativa.query.servico import Resultado

    settings.IATIVA_MANUTENCAO_AUTOMATICA = True
    u = faturamento['ana']
    c = conversas.nova_individual(u)
    m = Mensagem.objects.create(conversa=c, papel='usuario', texto=PEDIDO_EVENTOS)
    ac = objetivos.Acompanhamento(PEDIDO_EVENTOS, exig=objetivos.CALCULO,
                                  contrato=comparacao.contagens_mensais(PEDIDO_EVENTOS))
    ac.anotar('aprender_tabela', agente.ERRO_METADADOS)
    ac.anotar('criar_consulta', 'entrega_pendente', objetivos.CONSULTA)
    o = objetivos.registrar(ac, u, c, m.pk, SituacaoObjetivo.CONTINUAR)
    o.execucao = 'teste'
    o.save(update_fields=['execucao'])
    ac.registro = o
    ac.anotar('calcular_resultado', agente.ERRO_PARAMETROS)
    previa = agente.Saida('Consulta executada.', resultado=Resultado(True, 'Só a primeira contagem'))
    final = agente._encerrar_sem_cumprir(ac, u, c, m.pk, previa, previa, causa, [])
    assert final.resultado is None and 'Consulta executada' not in final.texto
    assert Objetivo.objects.get(pk=o.pk).situacao == SituacaoObjetivo.MANUTENCAO
