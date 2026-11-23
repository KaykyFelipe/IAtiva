"""M125: o contrato de entrega fica fiel ao pedido, mesmo quando o modelo transforma a pergunta em várias entregas.

Regressão do diagnóstico nº 24: "Quantos funcionários foram admitidos e desligados no mesmo ano, por filial, em
2026?" virou 4 entregas (admitidos, desligados, interseção, lista nominal); o ciclo, o revisor e o ensaio da
manutenção cobravam as 4 e a resposta certa (a interseção por filial) nunca era aceita.
"""
import sqlite3

import pytest

from iativa.accounts.filiais import rotulo
from iativa.agent import fidelidade, planejamento
from iativa.agent.models import SituacaoObjetivo
from iativa.agent.provedores import FakeProvider
from iativa.chat import conversas

from .test_tarefas import _d, base  # noqa: F401

PEDIDO = "Quantos funcionários foram admitidos e desligados no mesmo ano, por filial, em 2026?"
INTERSECAO_EXPLICITA = PEDIDO.replace("no mesmo ano", "ambos no mesmo ano")
QUATRO = [
    {'descricao': 'Contagem de funcionários admitidos em 2026, por filial', 'tipo': 'calculo', 'metrica': 'contagem'},
    {'descricao': 'Contagem de funcionários desligados em 2026, por filial', 'tipo': 'calculo', 'metrica': 'contagem'},
    {'descricao': 'Contagem de funcionários admitidos e desligados em 2026, por filial', 'tipo': 'calculo',
     'metrica': 'contagem'},
    {'descricao': 'Lista dos funcionários contados, com nomes e datas de admissão e desligamento, por filial',
     'tipo': 'consulta', 'metrica': ''},
]


def test_intersecao_vira_uma_entrega():
    assert [e['descricao'] for e in fidelidade.ajustar(PEDIDO, QUATRO)] == [QUATRO[2]['descricao']]


def test_sem_entrega_da_intersecao_o_contrato_ganha_uma():
    novo = fidelidade.ajustar(PEDIDO, QUATRO[:2] + QUATRO[3:])
    assert len(novo) == 1 and novo[0]['tipo'] == 'calculo' and novo[0]['metrica'] == 'contagem'
    assert 'admitidos e desligados no mesmo ano' in novo[0]['descricao'] and 'por filial' in novo[0]['descricao']


def test_eventos_sem_ligacao_continuam_separados_e_sem_lista_nao_pedida():
    texto = "Quantos funcionários foram admitidos e desligados em 2026, por filial?"
    assert [e['descricao'] for e in fidelidade.ajustar(texto, QUATRO)] == [e['descricao'] for e in QUATRO[:3]]


@pytest.mark.parametrize("texto", [
    "Quais funcionários foram admitidos e desligados no mesmo ano, por filial, em 2026? Liste os nomes.",
    "Quantos funcionários foram admitidos e desligados no mesmo ano em 2026 e quem são eles?",
])
def test_lista_pedida_continua_no_contrato(texto):
    novo = fidelidade.ajustar(texto, QUATRO)
    assert [e['descricao'] for e in novo] == [QUATRO[2]['descricao'], QUATRO[3]['descricao']]


def test_so_o_numero_tira_a_lista():
    assert fidelidade.ajustar("Quantos admitidos em 2026? Só o número.", [QUATRO[0], QUATRO[3]]) == [QUATRO[0]]


def test_remapeia_o_que_ja_foi_cumprido():
    novo = fidelidade.ajustar(PEDIDO, QUATRO)
    assert fidelidade.remapear_cumpridas(QUATRO, novo, [2, 3]) == [0]
    assert fidelidade.remapear_cumpridas(QUATRO, novo, [0, 3]) == []


@pytest.mark.django_db
def test_preparar_nao_aceita_as_quatro_entregas_da_segunda_leitura(base):
    provedor = FakeProvider(analises=[{
        'natureza': 'dados', 'setores': [base['cob'].codigo], 'confianca': 1, 'resumo': '', 'memorias': [],
        'entregas': [QUATRO[2]]}], releituras=[{'natureza': 'dados', 'entregas': QUATRO}])
    c = conversas.nova_individual(base['u'])
    a = planejamento.preparar(base['u'], c, PEDIDO, provedor, [])
    assert [e['descricao'] for e in a['entregas']] == [QUATRO[2]['descricao']]


@pytest.mark.django_db
def test_preparar_mantem_a_lista_quando_pedida(base):
    texto = "Quais funcionários foram admitidos e desligados no mesmo ano, por filial, em 2026? Liste os nomes."
    provedor = FakeProvider(analises=[{
        'natureza': 'dados', 'setores': [base['cob'].codigo], 'confianca': 1, 'resumo': '', 'memorias': [],
        'entregas': [QUATRO[2]]}], releituras=[{'natureza': 'dados', 'entregas': QUATRO}])
    c = conversas.nova_individual(base['u'])
    a = planejamento.preparar(base['u'], c, texto, provedor, [])
    assert QUATRO[3]['descricao'] in [e['descricao'] for e in a['entregas']]
    assert QUATRO[0]['descricao'] not in [e['descricao'] for e in a['entregas']]


def _erp(erp_sintetico):
    with sqlite3.connect(erp_sintetico) as conn:
        conn.execute("DELETE FROM SRA010")
        conn.executemany(
            "INSERT INTO SRA010 (RA_FILIAL, RA_MAT, RA_ADMISSA, RA_DEMISSA) VALUES (?, ?, ?, ?)",
            [("01", "000001", "20260101", "20260101"), ("01", "000002", "20261231", "20261231"),
             ("02", "000001", "20260201", "20260301"), ("01", "000003", "20251231", "20260101"),
             ("01", "000004", "20261231", "20270101"), ("02", "000002", "20260101", "        "),
             ("99", "000001", "20260101", "20261231")])


def _consulta(base):
    return _d(acao="preparar_consulta", objetivo=base["cm"].codigo, parametros=[
        {"codigo": "filial-analise", "valor": "TODAS"},
        {"codigo": "periodo-analise", "valor": "01/01/2026..31/12/2026"},
        {"codigo": "agregacao", "valor": "contagem:* por filial"},
        {"codigo": "filtro-campo", "valor": "RA_ADMISSA@20260101..20261231; RA_DEMISSA@20260101..20261231"},
    ])


@pytest.mark.django_db
@pytest.mark.parametrize("juncao", [False, True], ids=["sem-juncao", "consulta-com-juncao-de-descricao"])
def test_pergunta_do_diagnostico_24_entrega_a_intersecao_por_filial(base, erp_sintetico, juncao):
    """Ponta a ponta: a 2ª leitura do modelo tenta 4 entregas; a resposta é uma só, filial e quantidade, com as
    duas datas no mesmo vínculo (matrícula + filial, sem duplicar). No servidor, a consulta aprendida tem junção
    com a tabela de funções (só descrição): no cálculo ela sai, senão a prova da interseção não era registrada."""
    _erp(erp_sintetico)
    if juncao:
        base["cm"].spec["juncoes"] = [{"alias": "j1", "tabela": "SA1", "pares": [["A1_COD", "RA_MAT"],
                                                                               ["A1_LOJA", "RA_FILIAL"]]}]
        base["cm"].save(update_fields=["spec"])
    provedor = FakeProvider([_consulta(base)], analises=[{
        'natureza': 'dados', 'setores': [base['cob'].codigo], 'confianca': 1, 'resumo': '', 'memorias': [],
        'entregas': [QUATRO[2]]}], releituras=[{'natureza': 'dados', 'entregas': QUATRO}])
    conversa = conversas.nova_individual(base['u'])
    msg, res = conversas.enviar(base['u'], conversa, INTERSECAO_EXPLICITA, provedor)
    objetivo = conversa.objetivos.order_by('-pk').first()  # só existe quando o 1º passo não basta
    assert objetivo is None or (objetivo.situacao == SituacaoObjetivo.CUMPRIDO
                                and len(objetivo.contrato['entregas']) == 1), (objetivo.trilha, msg.texto)
    assert msg.ficha.get('tipo') != 'objetivo', msg.texto
    assert res.ok and res.colunas == ['Filial', 'Quantidade']
    assert {f: int(q) for f, q in res.linhas} == {rotulo('01'): 2, rotulo('02'): 1}
    assert 'Ainda falta' not in msg.texto and 'corrigindo' not in msg.texto.lower()
    t = msg.ficha['tempos_ms']  # M125: onde o tempo foi gasto (sem dados)
    assert {'total', 'leitura', 'decisao', 'erp'} <= set(t) and t['total'] >= t['erp']
    from io import StringIO
    from django.core.management import call_command
    out = StringIO()
    call_command('tempos_pedido', '--mensagem', msg.pk - 1, stdout=out)
    assert 'OK' in out.getvalue() and 'erp=' in out.getvalue(), out.getvalue()


def test_assinatura_igual_para_perguntas_equivalentes():
    from iativa.agent import receita_pedido as aprendizado
    a = aprendizado.assinatura(PEDIDO)
    b = aprendizado.assinatura("Em 2025, por filial, quantos funcionários tiveram admissão e desligamento no mesmo ano?")
    assert a == b and a["intersecao"] and a["eventos"] == ["admi", "desl"] and a["agrupamento"] == ["filial"]
    assert aprendizado.assinatura("Quantos funcionários foram admitidos em 2026, por filial?") != a


@pytest.mark.django_db
def test_receita_verificada_e_reaproveitada_sem_o_modelo(base, erp_sintetico):
    """1ª pergunta conferida grava a receita (lógica, sem dados); a 2ª, equivalente e de outro ano, é respondida
    pela receita sem nenhuma chamada de decisão ao modelo e sem código novo."""
    from iativa.agent.models import NotaAgente
    _erp(erp_sintetico)
    with sqlite3.connect(erp_sintetico) as conn:
        conn.executemany("INSERT INTO SRA010 (RA_FILIAL, RA_MAT, RA_ADMISSA, RA_DEMISSA) VALUES (?, ?, ?, ?)",
                         [("01", "000010", "20250105", "20250930"), ("02", "000010", "20250301", "20260102")])
    analise = {'natureza': 'dados', 'setores': [base['cob'].codigo], 'confianca': 1, 'resumo': '', 'memorias': [],
               'entregas': [QUATRO[2]]}
    conversa = conversas.nova_individual(base['u'])
    conversas.enviar(base['u'], conversa, INTERSECAO_EXPLICITA, FakeProvider([_consulta(base)], analises=[dict(analise)]))
    nota = NotaAgente.objects.get(criterios__has_key="receita", ativo=True)
    r = nota.criterios["receita"]
    assert r["objetivo"] == base["cm"].codigo and r["assinatura"]["intersecao"]
    assert ["filtro-campo", "RA_ADMISSA@{ANO}0101..{ANO}1231; RA_DEMISSA@{ANO}0101..{ANO}1231"] in r["parametros"]
    assert r["evidencia"]["revisada"] and r["versao"]
    assert "000001" not in nota.texto + str(nota.criterios)  # nenhum dado do ERP na memória

    analise2 = {**analise, 'entregas': [{'descricao': 'Contagem por filial de quem teve admissão e desligamento em '
                                                      '2025', 'tipo': 'calculo', 'metrica': 'contagem'}]}
    provedor = FakeProvider([], analises=[analise2])  # qualquer decisão pedida ao modelo falharia
    msg, res = conversas.enviar(base['u'], conversas.nova_individual(base['u']),
                                "Em 2025, por filial, quantos funcionários tiveram admissão e desligamento no mesmo ano?",
                                provedor)
    assert provedor.chamadas == 0, msg.texto
    assert res.ok and res.colunas == ['Filial', 'Quantidade']
    assert {f: int(q) for f, q in res.linhas} == {rotulo('01'): 1}


@pytest.mark.django_db
def test_receita_fora_do_acesso_da_pessoa_nao_e_usada(base):
    from iativa.agent import receita_pedido as aprendizado
    from iativa.agent.models import NotaAgente
    NotaAgente.objects.create(texto="Receita verificada", escopo="empresa", situacao="aprovada", tipo="termo",
                              criterios={"receita": {"assinatura": aprendizado.assinatura(PEDIDO),
                                                     "objetivo": "consulta-de-outro-setor", "parametros": []}})
    assert aprendizado.receita_para(PEDIDO, [base["cm"]]) == (None, None)


@pytest.mark.django_db
def test_revisor_indisponivel_nao_publica_e_preserva_o_pedido(base, erp_sintetico):
    _erp(erp_sintetico)

    class SemRevisor(FakeProvider):
        def planejar(self, entrada):
            if entrada.planejamento.get('modo') == 'revisar_entrega':
                raise RuntimeError("revisor fora do ar")
            return super().planejar(entrada)

    provedor = SemRevisor([_consulta(base)], analises=[{
        'natureza': 'dados', 'setores': [base['cob'].codigo], 'confianca': 1, 'resumo': '', 'memorias': [],
        'entregas': [QUATRO[2]]}])
    conversa = conversas.nova_individual(base['u'])
    msg, res = conversas.enviar(base['u'], conversa, INTERSECAO_EXPLICITA, provedor)
    objetivo = conversa.objetivos.latest('pk')
    assert objetivo.situacao == SituacaoObjetivo.CONTINUAR, (objetivo.trilha, msg.texto)
    assert res is None or not getattr(res, 'linhas', None), msg.texto  # nada sem conferência vai ao chat


@pytest.mark.django_db
def test_receita_composta_reusa_dois_passos_e_revalida_o_acesso(django_user_model):
    from types import SimpleNamespace
    from iativa.agent import receita_pedido

    u = django_user_model.objects.create_user("receita-composta")
    texto = "Quantos admitidos e desligados separadamente por filial em 2026?"
    contrato = [{"tipo": "calculo", "metrica": "contagem"}] * 2
    passos = [{"objetivo": cod, "parametros": [("periodo-analise", "01/01/2026..31/12/2026"),
                                               ("agregacao", "contagem:*")], "atende": [i]}
              for i, cod in enumerate(("admissoes", "desligamentos"))]
    receita_pedido.guardar_composta(u, texto, contrato, passos)
    permitidos = [SimpleNamespace(codigo=p["objetivo"]) for p in passos]
    nova = texto.replace("2026", "2025")
    fila, nota_id = receita_pedido.composta_para(nova, contrato, permitidos)
    assert nota_id and [d.atende for d in fila] == [(0,), (1,)]
    assert [dict(d.parametros)["periodo-analise"] for d in fila] == ["01/01/2025..31/12/2025"] * 2
    assert receita_pedido.composta_para(nova, contrato, permitidos[:1]) == ([], None)
    assert receita_pedido.composta_para(nova.replace("separadamente", "no mesmo ano"), contrato, permitidos) == ([], None)


@pytest.mark.django_db
@pytest.mark.parametrize("pedido", [PEDIDO, "Admitidos e desligados separadamente",
                                         "Quantas pessoas foram admitidas e demitidas em 2026?"])
def test_separadamente_herda_ano_e_entrega_duas_contagens(base, erp_sintetico, pedido, settings, monkeypatch):
    from django.http import QueryDict
    from iativa.agent import aprendizado

    _erp(erp_sintetico)
    with sqlite3.connect(erp_sintetico) as conn:
        conn.execute("UPDATE SRA010 SET RA_NOME='Ana Ribeiro' WHERE RA_FILIAL='01' AND RA_MAT='000001'")
    u = base["u"]
    q = QueryDict(mutable=True)
    q.update({"nome": "Admissões sem excluir desligados", "campo_data": "RA_ADMISSA"})
    q.setlist("colunas", ["RA_MAT", "RA_NOME", "RA_ADMISSA", "RA_DEMISSA"])
    q.setlist("setores", [str(base["cob"].pk)])
    aprendizado.aprovar(u, aprendizado.salvar_proposta(u, "SRA", q))
    c = conversas.nova_individual(u)
    c.mensagens.create(papel="usuario", texto=PEDIDO, autor=u)
    c.mensagens.create(papel="usuario", texto="Admitidos e desligados separadamente", autor=u)
    p = FakeProvider([], analises=[{'natureza': 'dados', 'setores': [base['cob'].codigo], 'confianca': 1,
                                   'resumo': '', 'memorias': [], 'entregas': QUATRO}])
    msg, res = conversas.enviar(u, c, pedido, p)
    assert p.chamadas == 0 and res is not None and res.consolidado, msg.texto
    por_filial = not pedido.startswith("Quantas")
    titulo = "Resumo por filial" if por_filial else "Resumo do período"
    assert [b.titulo for b in res.blocos] == ["Detalhes dos vínculos", titulo]
    detalhes, resumo = [b.resultado for b in res.blocos]
    assert detalhes.colunas == ["Nome", "Filial", "Função", "Data de admissão", "Data de desligamento"]
    assert len(detalhes.linhas) == 6  # os vínculos que estão nas duas consultas aparecem uma vez
    assert sum(linha[0] == "Ana Ribeiro" for linha in detalhes.linhas) == 1
    from iativa.chat.resultados import restaurar
    assert restaurar(u, msg.resultado).blocos[1].resultado.linhas[-1] == ["Total", "5", "4"]
    assert resumo.colunas == ["Filial", "Admitidos", "Desligados"]
    assert resumo.linhas[-1] == ["Total", "5", "4"]
    if por_filial:
        assert [rotulo('01'), "3", "3"] in resumo.linhas
        assert [rotulo('02'), "2", "1"] in resumo.linhas
    else:
        assert resumo.linhas == [["Total", "5", "4"]]
    msg_direta, direta = conversas.enviar(u, c, "Quero apenas o valor dos admitidos", FakeProvider([]))
    assert msg_direta.ficha["formato"] == "direto" and direta.curta == "5 admitidos"
    assert not c.objetivos.filter(situacao=SituacaoObjetivo.MANUTENCAO).exists()
    from iativa.agent.models import NotaAgente
    nota = NotaAgente.objects.get(criterios__receita__chave_composta__isnull=False)
    assert all(["periodo-analise", "{ANO}-01-01..{ANO}-12-31"] in passo["parametros"]
               for passo in nota.criterios["receita"]["passos"])
    if pedido == PEDIDO:
        from iativa.agent import agente, objetivos
        from iativa.manutencao.models import Incidente

        settings.IATIVA_AGENTE_CICLO = {"passos": 5, "continuacoes": 2, "espera_s": 0}
        original = agente._agir
        tentativas = []

        def agir(*args, **kwargs):
            decisao = args[2]
            tentativas.append(decisao.atende)
            if len(tentativas) == 1:
                return agente.Saida("", observacao=agente.Observacao(
                    decisao.acao, agente.TEMPO_ESGOTADO, "tempo limite sintético"))
            return original(*args, **kwargs)

        monkeypatch.setattr(agente, "_agir", agir)
        modelo_web = FakeProvider([])
        _, pendente = conversas.enviar(u, c, PEDIDO, modelo_web)
        assert pendente is None and modelo_web.chamadas == 0
        assert c.objetivos.latest("pk").situacao == SituacaoObjetivo.CONTINUAR
        modelo_worker = FakeProvider([])
        monkeypatch.setattr(agente, "obter_provedor", lambda **_: modelo_worker)
        assert objetivos.continuar_proximo()
        assert c.objetivos.latest("pk").situacao == SituacaoObjetivo.CUMPRIDO
        assert modelo_worker.chamadas == 0 and tentativas == [(0,), (0,), (1,)]
        assert NotaAgente.objects.filter(pk=nota.pk, ativo=True).exists()
        assert not Incidente.objects.exists()


@pytest.mark.django_db
def test_compoe_tres_consultas_verificadas_sem_nova_decisao(base, erp_sintetico):
    from django.http import QueryDict
    from iativa.agent import aprendizado, receita_pedido
    from iativa.agent.contratos import objetivos_para
    from iativa.agent.models import NotaAgente
    from iativa.manutencao.models import Incidente

    _erp(erp_sintetico)
    u, cm = base["u"], base["cm"]
    anos = ("2023", "2024", "2025")
    with sqlite3.connect(erp_sintetico) as conn:
        conn.executemany("INSERT INTO SRA010 (RA_FILIAL, RA_MAT, RA_ADMISSA, RA_DEMISSA) VALUES (?, ?, ?, ?)",
                         [("01", f"0000{i + 10}", ano + "0201", ano + "0301") for i, ano in enumerate(anos)])

    q = QueryDict(mutable=True)
    q.update({"nome": "Admissões", "campo_data": "RA_ADMISSA"})
    q.setlist("colunas", ["RA_MAT", "RA_ADMISSA"])
    q.setlist("setores", [str(base["cob"].pk)])
    admissao = aprendizado.aprovar(u, aprendizado.salvar_proposta(u, "SRA", q))

    def analise(entregas):
        return {"natureza": "dados", "setores": [base["cob"].codigo], "confianca": 1,
                "resumo": "", "memorias": [], "entregas": entregas}

    itens = [{"descricao": f"Funcionários {evento} por filial em {ano}", "tipo": "calculo", "metrica": "contagem"}
             for evento, ano in zip(("desligados", "desligados", "admitidos"), anos)]
    c = conversas.nova_individual(u)
    adm_codigo = next(o.codigo for o in objetivos_para(u) if o.modelo_id == admissao.pk)
    for codigo, item in [(cm.codigo, itens[0]), (adm_codigo, itens[2])]:
        ano = item["descricao"][-4:]
        modelo = FakeProvider([_d(acao="preparar_consulta", objetivo=codigo, entrega="calculo", parametros=[
            {"codigo": "periodo-analise", "valor": f"01/01/{ano}..31/12/{ano}"},
            {"codigo": "filial-analise", "valor": "TODAS"},
            {"codigo": "agregacao", "valor": "contagem:*"},
        ])], analises=[analise([item])])
        _, simples = conversas.enviar(u, c, f"Quantos {item['descricao'].lower()}?", modelo)
        assert simples is not None and simples.ok
    assert NotaAgente.objects.filter(criterios__receita__has_key="componentes").count() == 2

    assert receita_pedido.compor_partes(itens, []) == []
    contrato_misto = itens[:2] + [{**itens[2], "descricao": itens[2]["descricao"] + " somente da Matriz"}]
    assert [d.atende for d in receita_pedido.compor_partes(
        contrato_misto, objetivos_para(u))] == [(0,), (1,)]
    outro = FakeProvider([], analises=[analise(itens)])
    _, combinado = conversas.enviar(
        u, c, "Mostre por filial quantos saíram em 2023, quantos saíram em 2024 e quantos entraram em 2025.", outro)
    assert outro.chamadas == 0 and combinado is not None and combinado.ok
    assert combinado.colunas == ["Filial", *[e["descricao"] for e in itens]]
    assert combinado.linhas[-1] == ["Total", "1", "1", "2"]
    assert not Incidente.objects.exists()


def test_m127_agregacao_no_banco_e_calculo_mesmo_sem_parametro_na_decisao():
    """"Quantos funcionários ativos temos hoje por filial?": a consulta veio por aprender_tabela (decisão sem o
    parâmetro agregacao); o resultado agregado por filial é cálculo e cumpre a entrega."""
    from types import SimpleNamespace
    from iativa.agent.objetivos import CALCULO, evidencia
    res = SimpleNamespace(ok=True, consolidado=False, agregado=True, medida_calculada='', linhas=[["01", 3], ["02", 5]],
                          colunas=["Filial", "Quantidade"], totais=None, escopo={})
    d = SimpleNamespace(acao='aprender_tabela', parametros=(), objetivo='')
    assert evidencia(d, SimpleNamespace(ficha={}, observacao=None, resultado=res), "Quantos ativos por filial?") == CALCULO
