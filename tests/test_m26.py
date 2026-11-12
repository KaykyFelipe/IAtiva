"""M26: pedido de verificação continua depois da lista até virar tarefa; "Pensando…" ao vivo; barra fixa."""
import uuid

import pytest
from django.urls import reverse

from iativa.agent import andamento, tarefas
from iativa.agent.models import Tarefa
from iativa.agent.provedores import FakeProvider
from iativa.chat import conversas

from .test_tarefas import CAMPOS, LISTA, _d, _lista, base  # noqa: F401

pytestmark = pytest.mark.django_db

PEDIDO = "verifique se os demitidos dos ultimos 2 meses teve uma matricula nova, que significa que o cargo foi trocado"
LISTAR = {"codigo": "filial-analise", "valor": "TODAS"}, {"codigo": "periodo-analise", "valor": "12/09/2026..12/11/2026"}


def _listar(b):
    return _d(acao="preparar_consulta", objetivo=b["cm"].codigo, parametros=list(LISTAR))


def _investigar():
    return _d(acao="investigar_lista", investigacao={"campos": CAMPOS, "identidade": "nome"})


def test_lista_e_etapa_e_a_verificacao_segue_na_mesma_mensagem(base):
    c = _lista(base)
    p = FakeProvider([_listar(base), _investigar()])
    msg, res = conversas.enviar(base["u"], c, PEDIDO, p)
    assert p.chamadas == 2  # não parou depois de refazer a lista
    assert res is not None and res.ok  # a lista refeita continua na tela
    t = Tarefa.objects.get()
    assert msg.ficha["tipo"] == "tarefa" and msg.ficha["tarefa"] == t.pk
    assert "Consulta executada" in msg.texto and "todas as pessoas" in msg.texto
    tarefas.processar_proxima()
    t.refresh_from_db()
    assert t.situacao == "concluida" and t.cobertura["total"] == len(res.linhas) == 7  # período 12/09 a 12/11


def test_pedido_sem_lista_anterior_tambem_continua(base):
    c = conversas.nova_individual(base["u"])
    p = FakeProvider([_listar(base), _investigar()])
    msg, res = conversas.enviar(base["u"], c, PEDIDO, p)
    assert p.chamadas == 2 and msg.ficha["tipo"] == "tarefa" and res.ok


def test_lista_simples_nao_vira_verificacao(base):
    c = conversas.nova_individual(base["u"])
    p = FakeProvider([_listar(base), _investigar()])
    msg, res = conversas.enviar(base["u"], c, "liste os demitidos dos ultimos 2 meses", p)
    assert p.chamadas == 1 and not Tarefa.objects.exists() and res.ok


def test_modelo_nao_pode_encerrar_so_com_a_lista(base):
    """Regressão (revisão M30): depois da lista, "a lista basta" não encerra o pedido de verificação."""
    c = conversas.nova_individual(base["u"])
    p = FakeProvider([_listar(base), _d(resposta="A lista já mostra o que você pediu."),
                      _d(resposta="Não precisa verificar mais nada.")])
    msg, res = conversas.enviar(base["u"], c, PEDIDO, p)
    assert res.ok and not Tarefa.objects.exists()
    assert "A lista já mostra" not in msg.texto and "ainda não foi feita" in msg.texto
    assert msg.ficha.get("verificacao_pendente")


def test_depois_de_recusado_o_modelo_investiga(base):
    c = conversas.nova_individual(base["u"])
    p = FakeProvider([_listar(base), _d(resposta="A lista já mostra o que você pediu."), _investigar()])
    msg, res = conversas.enviar(base["u"], c, PEDIDO, p)
    assert msg.ficha["tipo"] == "tarefa" and Tarefa.objects.count() == 1


def test_pergunta_a_pessoa_e_permitida(base):
    c = conversas.nova_individual(base["u"])
    p = FakeProvider([_listar(base), _d(resposta="Qual campo indica a recontratação na sua empresa?")])
    msg, res = conversas.enviar(base["u"], c, PEDIDO, p)
    assert msg.texto.strip().endswith("?") and "ainda não foi feita" not in msg.texto

def test_pensando_mostra_as_etapas_reais(base, client):
    c = _lista(base)
    pedido = uuid.uuid4()
    conversas.enviar(base["u"], c, PEDIDO, FakeProvider([_listar(base), _investigar()]), pedido=pedido)
    passos = andamento.ler(base["u"].pk, pedido)["passos"]
    assert passos[0] == "Lendo o pedido e o histórico da conversa"
    for esperado in ("Decidindo o que fazer", "Consultando o ERP", "Lista pronta; agora vou verificar cada pessoa",
                     "Verificação de toda a lista agendada em segundo plano", "Escrevendo a resposta"):
        assert any(p.startswith(esperado) for p in passos), (esperado, passos)
    assert not any("SINTETIC" in p for p in passos)  # nada do ERP
    client.force_login(base["u"])
    r = client.get(reverse("chat:andamento", args=[pedido]))
    assert r.json()["fim"] and r.json()["passos"] == passos and "no-store" in r["Cache-Control"]


def test_andamento_de_outra_pessoa_vem_vazio(base, client, novo_usuario):
    pedido = uuid.uuid4()
    with andamento.acompanhar(base["u"].pk, pedido):
        andamento.avisar("Consultando o ERP — Matriz")
    outro = novo_usuario("outro", base["cob"])
    client.force_login(outro)
    assert client.get(reverse("chat:andamento", args=[pedido])).json() == {"passos": [], "fim": False}


def test_barra_fixa_acompanha_a_tarefa(base, client):
    c = _lista(base)
    conversas.enviar(base["u"], c, PEDIDO, FakeProvider([_investigar()]))
    client.force_login(base["u"])
    html = client.get(reverse("chat:conversa", args=[c.pk])).content.decode()
    t = Tarefa.objects.get()
    assert 'id="atividade-fixa"' in html and f'id="tarefa-{t.pk}"' in html and "acompanhar" not in html
    assert html.index(f'id="tarefa-{t.pk}"') > html.index("data-resultado-de")  # cartão abaixo do resultado
    assert html.index('id="atividade-fixa"') > html.index('class="compositor"')  # barra dentro do compositor
    tarefas.processar_proxima()
    barra = client.get(reverse("chat:atividade", args=[c.pk])).content.decode()
    assert "hidden" in barra and "hx-trigger" not in barra and "data-tarefa-ativa" not in barra  # some ao terminar


# --- recuperação de falhas do banco no worker ---------------------------------------------------------

class FalhaBanco(Exception):
    """Imita o pyodbc.OperationalError do SQL Server (SQLSTATE no primeiro argumento)."""


def _falhar_quando(monkeypatch, condicao, estado="HYT00"):
    from iativa.agent import investigacao as inv

    original = inv._consultar

    def talvez(cat, plano, ctl, limite, timeout=None):
        if condicao(plano):
            raise FalhaBanco(estado, "Query timeout expired")
        return original(cat, plano, ctl, limite, timeout)

    monkeypatch.setattr(inv, "_consultar", talvez)


def _tarefa_pronta(b):
    c = _lista(b)
    conversas.enviar(b["u"], c, PEDIDO, FakeProvider([_investigar()]))
    return Tarefa.objects.get()


def _tem_nome(plano, nome):
    return any(f.op == "IN" and any(nome in str(v) for v in f.valor) for f in plano.filtros)


def test_lote_que_nao_responde_e_dividido_e_a_tarefa_conclui(base, monkeypatch):
    t = _tarefa_pronta(base)
    _falhar_quando(monkeypatch, lambda p: _tem_nome(p, "BRUNO") and p.filial == "01")
    tarefas.processar_proxima()
    t.refresh_from_db()
    assert t.situacao == "concluida" and t.cobertura["total"] == len(LISTA)
    assert t.cobertura["inconclusivos"] >= 1
    correcoes = " ".join(t.consumo["correcoes"])
    assert "passei a buscar em lotes de" in correcoes and "marquei como inconclusivo" in correcoes
    bruno = next(i for i in tarefas.conclusao(base["u"], t)["itens"] if i["nome"].startswith("BRUNO"))
    assert bruno["verificado"] is False and "não respondeu" in bruno["texto"]  # o motivo aparece na conclusão
    assert "BRUNO" not in str(t.consumo)


def test_falha_passageira_na_lista_e_repetida(base, monkeypatch):
    t = _tarefa_pronta(base)
    vezes = {"n": 0}

    def primeira_falha(p):
        if p.objetivo != "investigacao" and vezes["n"] == 0:
            vezes["n"] += 1
            return True
        return False

    _falhar_quando(monkeypatch, primeira_falha)
    tarefas.processar_proxima()
    t.refresh_from_db()
    assert t.situacao == "concluida" and any("tentando de novo" in x for x in t.consumo["correcoes"])


def test_erro_inesperado_tenta_corrigir_antes_de_desistir(base, monkeypatch):
    from iativa.agent import investigacao as inv

    t = _tarefa_pronta(base)
    monkeypatch.setattr(tarefas.time, "sleep", lambda s: None)

    def quebra(*a, **kw):
        raise KeyError("x")

    monkeypatch.setattr(inv, "classificar", quebra)
    p = FakeProvider([_investigar()])
    monkeypatch.setattr(tarefas, "obter_provedor", lambda usuario=None: p)
    tarefas.processar_proxima()
    t.refresh_from_db()
    assert t.situacao == "falhou" and p.chamadas == 1  # tentou de novo e ainda refez o plano com o modelo
    assert "Tentei 3 vezes e pedi outro caminho ao modelo" in t.motivo
    assert sum("tentando de novo" in x for x in t.consumo["correcoes"]) >= 2


def test_atualizacoes_nao_substituem_a_conversa_e_conclusao_aparece_sozinha(base, client):
    c = _lista(base)
    conversas.enviar(base["u"], c, PEDIDO, FakeProvider([_investigar()]))
    client.force_login(base["u"])
    t = Tarefa.objects.get()
    barra = client.get(reverse("chat:atividade", args=[c.pk])).content.decode()
    cartao = client.get(reverse("chat:tarefa", args=[t.pk])).content.decode()
    assert 'hx-target="this"' in barra and 'hx-target="this"' in cartao  # não herdam o alvo #conversa
    tarefas.processar_proxima()
    cartao = client.get(reverse("chat:tarefa", args=[t.pk])).content.decode()
    assert "Ver conclusão" not in cartao and 'hx-trigger="load"' in cartao
    html = client.get(reverse("chat:tarefa_conclusao", args=[t.pk])).content.decode()
    assert "conclusao-tabela" in html and "Inconclusivo" in html or "Verificado" in html


def test_conclusao_fica_salva_e_recarregar_nao_consulta_de_novo(base, client, monkeypatch):
    from iativa.agent import investigacao as inv

    t = _tarefa_pronta(base)
    tarefas.processar_proxima()
    t.refresh_from_db()
    assert t.conclusao["cobertura"]["total"] == len(LISTA)

    def proibido(*a, **kw):
        raise AssertionError("consultou o ERP de novo")

    monkeypatch.setattr(inv, "_consultar", proibido)
    client.force_login(base["u"])
    for _ in range(2):  # recarregar a página
        html = client.get(reverse("chat:tarefa_conclusao", args=[t.pk])).content.decode()
        assert all(n in html for n in LISTA) and "verificação feita em" in html



# --- revisão M30: datas, reconstrução única, aguardando usuário ---------------------------------------

def _plano_simples(base):
    from iativa.agent import investigacao as inv
    from iativa.chat.models import RequestContext

    _lista(base)
    sol = RequestContext.objects.filter(objetivo=base["cm"].codigo, estado="concluida").latest("id")
    return inv.validar_plano(base["u"], sol, {"campos": CAMPOS, "identidade": "nome"})


def _item(inv, inicio, fim, outros=()):
    reg = inv.Registro("01", ("000900",), "PESSOA TESTE", {"inicio": inicio, "fim": fim})
    ctx = inv.Contexto()
    it = inv.Item(reg, "PESSOA TESTE")
    ctx.itens = [it]
    ctx.relacionados = {"PESSOA TESTE": [reg] + [inv.Registro("02", ("000901",), "PESSOA TESTE", o) for o in outros]}
    return ctx, it


def test_desligamento_futuro_nao_e_desligamento_ocorrido(base, monkeypatch):
    from datetime import date

    from iativa.agent import investigacao as inv

    plano = _plano_simples(base)
    monkeypatch.setattr(inv.timezone, "localdate", lambda: date(2026, 11, 12))
    ctx, it = _item(inv, "20250101", "20261231")
    inv.classificar(plano, ctx)
    assert it.classe == "fim_futuro" and "ainda não chegou" in it.texto and "encerrado em" not in it.texto


def test_datas_impossiveis_ou_invertidas_ficam_inconclusivas(base):
    from iativa.agent import investigacao as inv

    plano = _plano_simples(base)
    for ini, fim in (("20260301", "20260101"), ("20260101", "20260231")):
        ctx, it = _item(inv, ini, fim)
        inv.classificar(plano, ctx)
        assert it.classe == "datas_inconsistentes" and it.verificado is False


def test_novo_vinculo_futuro_e_dito_como_previsto(base, monkeypatch):
    from datetime import date

    from iativa.agent import investigacao as inv

    plano = {**_plano_simples(base), "identidade": "documento"}
    monkeypatch.setattr(inv.timezone, "localdate", lambda: date(2026, 11, 12))
    ctx, it = _item(inv, "20250101", "20261010", outros=[{"inicio": "20261201", "fim": ""}])
    inv.classificar(plano, ctx)
    assert it.classe == "novo_vinculo_posterior" and "ainda não começou" in it.texto


def test_ultima_data_da_base_ignora_datas_futuras(base, monkeypatch):
    from datetime import date

    from iativa.agent import investigacao as inv

    monkeypatch.setattr(inv.timezone, "localdate", lambda: date(2026, 11, 12))
    ctx = inv.Contexto()
    inv._ver_datas(ctx, inv.Registro("01", ("1",), "X", {"inicio": "20261101", "fim": "20270101"}))
    assert ctx.ultima_data == "20261101"


def test_conclusao_perdida_e_refeita_no_worker_uma_vez(base, client, monkeypatch):
    from iativa.agent import investigacao as inv

    t = _tarefa_pronta(base)
    tarefas.processar_proxima()
    Tarefa.objects.filter(pk=t.pk).update(conclusao={})  # conclusão perdida (tarefa antiga)
    client.force_login(base["u"])
    consultas = {"n": 0}
    original = inv._consultar

    def contar(*a, **kw):
        consultas["n"] += 1
        return original(*a, **kw)

    monkeypatch.setattr(inv, "_consultar", contar)
    for _ in range(3):  # várias atualizações da tela: nenhuma consulta na requisição web
        html = client.get(reverse("chat:tarefa_conclusao", args=[t.pk])).content.decode()
        assert "Refazendo a conclusão" in html
    assert consultas["n"] == 0 and Tarefa.objects.get(pk=t.pk).reconstruir
    assert tarefas.processar_proxima()  # o worker refaz uma vez
    assert not tarefas.reconstruir_proxima()
    t.refresh_from_db()
    assert t.conclusao and not t.reconstruir and consultas["n"] > 0
    html = client.get(reverse("chat:tarefa_conclusao", args=[t.pk])).content.decode()
    assert all(n in html for n in LISTA)


def test_reconstrucao_nao_roda_em_dobro(base):
    from datetime import timedelta

    from django.utils import timezone

    t = _tarefa_pronta(base)
    tarefas.processar_proxima()
    Tarefa.objects.filter(pk=t.pk).update(conclusao={})
    assert tarefas.solicitar_reconstrucao(t) and not tarefas.solicitar_reconstrucao(t)
    Tarefa.objects.filter(pk=t.pk).update(execucao="outro", lease_ate=timezone.now() + timedelta(seconds=60))
    assert not tarefas.reconstruir_proxima()  # outro worker está refazendo


def test_pergunta_aparece_e_a_resposta_retoma_a_mesma_tarefa(base, monkeypatch):
    from iativa.chat.models import Mensagem

    t = _tarefa_pronta(base)
    Tarefa.objects.filter(pk=t.pk).update(proposta={"campos": {**CAMPOS, "fim": "RA_NAOEXISTE"}})
    pergunta = _d(resposta="Qual campo guarda a data de desligamento?")
    monkeypatch.setattr(tarefas, "obter_provedor", lambda usuario=None: FakeProvider([pergunta]))
    tarefas.processar_proxima()
    t.refresh_from_db()
    assert t.situacao == "aguardando_usuario"
    assert Mensagem.objects.filter(conversa=t.conversa, texto__contains="Qual campo guarda").exists()

    vistos = {}

    class Espiao(FakeProvider):
        def decidir(self, entrada):
            vistos["lista"] = entrada.lista_anterior
            return super().decidir(entrada)

    msg, _ = conversas.enviar(base["u"], t.conversa, "é o RA_DEMISSA", Espiao([_investigar()]))
    assert "Qual campo guarda" in vistos["lista"]["pergunta_pendente"]
    assert Tarefa.objects.count() == 1 and msg.ficha["tarefa"] == t.pk
    t.refresh_from_db()
    assert t.situacao == "pendente" and t.proposta["campos"]["fim"] == "RA_DEMISSA"
    tarefas.processar_proxima()
    t.refresh_from_db()
    assert t.situacao == "concluida" and t.conclusao["cobertura"]["total"] == len(LISTA)


def test_retomada_reconfere_permissao(base):
    from iativa.accounts.models import AccessPolicy

    t = _tarefa_pronta(base)
    Tarefa.objects.filter(pk=t.pk).update(situacao="aguardando_usuario", motivo="Qual campo?")
    AccessPolicy.objects.filter(user=base["u"]).update(pode_consultar=False)
    conversas.enviar(base["u"], t.conversa, "é o RA_DEMISSA", FakeProvider([_investigar()]))
    t.refresh_from_db()
    assert t.situacao == "aguardando_usuario"  # não retomou sem permissão
