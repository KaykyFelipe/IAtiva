"""M48: resposta no formato pedido, consultas da conversa visíveis à IA e aviso de correção automática."""
import re

import pytest
from django.urls import reverse

from iativa.agent.decisao import DecisaoInvalida, validar
from iativa.agent.provedores import _contexto_agente
from iativa.chat import conversas
from iativa.query.servico import Medida, Resultado, Totais
from .test_calculos_m45 import estagio  # noqa: F401
from .test_ciclo_agente import Espiao
from .test_historico_funcional import funcional  # noqa: F401
from .test_tarefas import _d, _lista, base  # noqa: F401

pytestmark = pytest.mark.django_db


def test_decisao_aceita_formato_e_recusa_desconhecido():
    assert validar(_d(formato="direto"), [], []).formato == "direto"
    assert validar(_d(formato="resumo"), [], []).formato == "resumo"
    assert validar(_d(), [], []).formato == ""
    with pytest.raises(DecisaoInvalida):
        validar(_d(formato="longo"), [], [])


def test_resposta_curta_de_cada_tipo_de_resultado():
    dias = Resultado(True, "x", colunas=["Período", "Início", "Fim", "Dias úteis", "Base"],
                     linhas=[["Estágio", "01/01/2026", "01/10/2026", "195", "Período registrado"]],
                     medida_calculada="dias_uteis")
    assert dias.curta == "195 dias úteis"
    soma = Resultado(True, "x", colunas=["Cálculo", "Resultado"], linhas=[["Soma", "1234.5"]], medida_calculada="soma")
    assert soma.curta == "1.234,50"
    uma = Resultado(True, "x", colunas=["Filial", "Quantidade"], tipos=["texto", "inteiro"], linhas=[["01", "731"]])
    assert uma.curta == "731"
    total = Resultado(True, "x", colunas=["Cliente", "Valor"], tipos=["texto", "dinheiro"],
                      linhas=[["A", "R$ 1,00"], ["B", "R$ 2,00"]],
                      totais=Totais(registros=2, medidas=[Medida("Total", "SUM", "dinheiro", 3)]))
    assert total.curta == "R$ 3,00"
    lista = Resultado(True, "x", colunas=["Nome"], tipos=["texto"], linhas=[["A"], ["B"]])
    assert lista.curta == ""  # lista: a tabela aparece, sem extras


def test_apenas_os_dias_uteis_mostra_so_o_valor(estagio, client):
    b, c, origem = estagio
    msg, res = conversas.enviar(b["u"], c, "quero que você me diga apenas os dias úteis", Espiao([
        _d(acao="calcular_resultado", formato="direto", resposta="Aqui está.",
           calculo={"operacao": "dias_uteis", "mensagem": origem.pk})]))
    assert res and res.curta == "195 dias úteis"
    assert msg.ficha["formato"] == "direto" and msg.resultado["curta"] == "195 dias úteis"
    client.force_login(b["u"])
    html = client.get(reverse("chat:conversa", args=[c.pk])).content.decode()
    trecho = html[html.index(f'data-resultado-de="{msg.pk}"'):]
    trecho = trecho[:trecho.index("</div>", trecho.index("resposta-direta")) + 6]
    assert "<strong>195 dias úteis</strong>" in trecho and "<table>" not in trecho
    assert "Critérios e premissas" not in trecho and "Aqui está." not in html


def test_ia_ve_as_consultas_da_conversa_sem_numeros(base):
    c = _lista(base)
    p = Espiao([_d(resposta="Usei a filial TODAS e o período pedido.")])
    conversas.enviar(base["u"], c, "quais filtros você usou nessa consulta?", p)
    consultas = p.entradas[0].consultas_conversa
    assert consultas and consultas[-1]["codigo"] == base["cm"].codigo
    item = consultas[-1]
    assert item["tabelas"] and item["colunas"] and item["criterios"] and item["mensagem"]
    contexto = _contexto_agente(p.entradas[0])
    assert "consultas_desta_conversa" in contexto
    assert not re.search(r"R\$\s*\d", contexto)  # nenhum valor do ERP vai ao modelo


def test_contexto_traz_hora_do_servidor():
    from datetime import datetime
    from unittest import mock

    from django.utils import timezone

    from iativa.agent.decisao import EntradaAgente

    agora = timezone.make_aware(datetime(2026, 11, 13, 18, 25, 7))
    with mock.patch("django.utils.timezone.localtime", return_value=agora):
        ctx = _contexto_agente(EntradaAgente(mensagem="que horas?", hoje="2026-11-13", historico=[], objetivos=[],
                                             solicitacao_atual=None))
    assert "13/11/2026 18:25:07 (sexta-feira" in ctx
