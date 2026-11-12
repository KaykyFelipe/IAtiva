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
    assert "Concluída" in barra and "hx-trigger" not in barra  # parou de consultar
