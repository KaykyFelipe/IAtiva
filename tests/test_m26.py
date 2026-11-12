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


def test_modelo_decide_que_a_lista_basta(base):
    c = conversas.nova_individual(base["u"])
    p = FakeProvider([_listar(base), _d(resposta="A lista já mostra o que você pediu.")])
    msg, res = conversas.enviar(base["u"], c, PEDIDO, p)
    assert res.ok and "A lista já mostra" in msg.texto and not Tarefa.objects.exists()


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
    bruno = next(i for i in tarefas.conclusao(base["u"], t).itens if i.reg.nome.startswith("BRUNO"))
    assert bruno.verificado is False and "não respondeu" in bruno.texto  # o motivo aparece na conclusão
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
