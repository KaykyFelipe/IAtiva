"""Regressões da revisão: filtros preservados, ranking completo e conclusão comprovada."""
import sqlite3

import pytest
from django.core.exceptions import ValidationError

from iativa.agent import aprendizado
from iativa.agent.models import ConsultaModelo, FiltroUsuario, Objetivo, SituacaoObjetivo
from iativa.agent.provedores import FakeProvider
from iativa.chat import conversas

from .test_ajuste_filtro import ana, _consulta, _d, _rodar, _ti, FILIAL  # noqa: F401

pytestmark = pytest.mark.django_db


def test_criacao_com_filtro_invalido_nao_aprova_consulta_sem_filtro(ana):
    with pytest.raises(ValidationError):
        aprendizado.criar_automatico(ana, {
            "tabela": "SE1", "nome": "Filtro indispensavel", "colunas": ["E1_VALOR"],
            "filtros": [{"campo": "F4_DUPLIC", "operador": "igual", "valor": "S"}],
        })
    assert not ConsultaModelo.objects.filter(nome="Filtro indispensavel").exists()


def test_consulta_existente_nao_ignora_filtro_diferente(ana):
    c = conversas.nova_individual(ana)
    cm, _ = _consulta(ana, c)
    with pytest.raises(ValidationError, match="outros filtros"):
        aprendizado.criar_automatico(ana, {
            "tabela": "SE1", "nome": cm.nome, "colunas": ["E1_VALOR"],
            "filtros": [{"campo": "E1_TIPO", "operador": "igual", "valor": "NF"}],
        })
    cm.refresh_from_db()
    assert cm.spec["filtros_fixos"] == []  # reutilizar nome não altera a consulta de todos


def test_voltar_filtro_global_preserva_juncao_do_filtro_pessoal(ana):
    _ti(ana)
    c = conversas.nova_individual(ana)
    cm, _ = _consulta(ana, c)
    cm = aprendizado.ajustar_filtros(ana, cm, [
        {"campo": "A1_XSEGMEN", "operador": "igual", "valor": "COM"}], [])
    aprendizado.ajustar_filtros(ana, cm, [
        {"campo": "A1_XSEGMEN", "operador": "igual", "valor": "COM"}], [], alcance="usuario")
    antes = _rodar(ana, c, cm)
    cm = aprendizado.voltar_filtros(ana, cm)
    assert _rodar(ana, c, cm) == antes


def test_camada_antiga_referenciando_juncao_global_tambem_sobrevive(ana):
    _ti(ana)
    c = conversas.nova_individual(ana)
    cm, _ = _consulta(ana, c)
    cm = aprendizado.ajustar_filtros(ana, cm, [
        {"campo": "A1_XSEGMEN", "operador": "igual", "valor": "COM"}], [])
    FiltroUsuario.objects.create(usuario=ana, consulta=cm, camada={"filtros": [
        {"campo": "A1_XSEGMEN", "op": "=", "valor": "COM", "alias": "j1"}],
        "juncoes": [], "desligados": ["A1_XSEGMEN"]})
    antes = _rodar(ana, c, cm)
    cm = aprendizado.voltar_filtros(ana, cm)
    assert _rodar(ana, c, cm) == antes


def test_ranking_global_inclui_grupo_que_nao_e_top_em_nenhuma_filial(ana, erp_sintetico, politica):
    from .test_ranking_filiais import _rodar as ranking
    from iativa.accounts.models import Department

    politica(ana, Department.objects.get(codigo="financeiro-cobranca"), consultar=True, filiais=["02"])
    with sqlite3.connect(erp_sintetico) as con:
        con.row_factory = sqlite3.Row
        modelo = dict(con.execute("SELECT * FROM SE1010 LIMIT 1").fetchone())
        con.execute("DELETE FROM SE1010")
        for n, (filial, cliente, valor) in enumerate([
            ("01", "A", 100), ("01", "C", 90), ("02", "B", 100), ("02", "C", 90)], 1):
            row = {**modelo, "E1_FILIAL": filial, "E1_CLIENTE": cliente, "E1_NUM": f"{n:06}",
                   "E1_VALOR": valor, "E1_SALDO": valor, "R_E_C_N_O_": n, "D_E_L_E_T_": " "}
            con.execute(f"INSERT INTO SE1010 ({','.join(row)}) VALUES ({','.join('?' for _ in row)})", tuple(row.values()))
    _, res = ranking(ana, "soma:E1_VALOR por E1_CLIENTE top 1")
    assert res.ok and res.linhas[0][0] == "C"
    assert res.linhas[0][-1] == "180,00"


def test_segunda_pergunta_sem_execucao_nao_marca_pedido_cumprido(ana):
    c = conversas.nova_individual(ana)
    p = FakeProvider([
        _d(acao="preparar_consulta", objetivo="titulos-a-vencer", entrega="calculo", parametros=FILIAL + [
            {"codigo": "tratamento-baixa-parcial", "valor": "considerar-saldo"},
            {"codigo": "periodo-analise", "valor": "01/01/2020..31/12/2030"}]),
        _d(acao="conversar", resposta="Já rodei as duas consultas."),
    ])
    msg, res = conversas.enviar(ana, c, "quanto vamos receber? e quanto está em atraso?", p)
    assert res is not None and res.ok
    assert "Já rodei" not in msg.texto
    assert not Objetivo.objects.filter(conversa=c, situacao=SituacaoObjetivo.CUMPRIDO).exists()


def test_dois_ajustes_diferentes_no_mesmo_ciclo_nao_sao_repeticao(ana):
    c = conversas.nova_individual(ana)
    cm, _ = _consulta(ana, c)
    p = FakeProvider([
        _d(acao="ajustar_filtro", alvo=cm.nome, entrega="calculo",
           ajuste={"filtros": [{"campo": "A1_XSEGMEN", "operador": "igual", "valor": "COM"}]}),
        _d(acao="ajustar_filtro", alvo=cm.nome, entrega="calculo",
           ajuste={"filtros": [{"campo": "E1_TIPO", "operador": "igual", "valor": "NF"}]}),
        _d(acao="preparar_consulta", objetivo=cm.codigo,
           parametros=FILIAL + [{"codigo": "agregacao", "valor": "soma:E1_VALOR"}]),
    ])
    msg, res = conversas.enviar(ana, c, "ajuste para mim: comércio e tipo NF, depois some os valores", p)
    fu = FiltroUsuario.objects.get(usuario=ana, consulta=cm)
    assert {f["campo"] for f in fu.camada["filtros"]} == {"A1_XSEGMEN", "E1_TIPO"}
    assert res is not None and res.ok, msg.texto


def test_ranking_incompleto_nao_afirma_lider_geral(ana, settings, politica):
    from .test_ranking_filiais import _rodar as ranking
    from iativa.accounts.models import Department

    politica(ana, Department.objects.get(codigo="financeiro-cobranca"), consultar=True, filiais=["02"])
    settings.IATIVA_CONSULTA = {**settings.IATIVA_CONSULTA, "limite_linhas": 1}
    msg, res = ranking(ana, "soma:E1_VALOR por E1_CLIENTE top 1")
    assert res is None or not res.ok
    assert "ranking geral" in msg.texto.lower() and "não foi validado" in msg.texto
    assert "O maior é" not in msg.texto


def test_resposta_e_memoria_nao_encerram_antes_da_segunda_pergunta(ana):
    from iativa.agent.models import NotaAgente

    c = conversas.nova_individual(ana)
    comum = FILIAL + [{"codigo": "tratamento-baixa-parcial", "valor": "considerar-saldo"}]
    p = FakeProvider([
        _d(acao="preparar_consulta", objetivo="titulos-a-vencer", entrega="calculo", parametros=comum + [
            {"codigo": "periodo-analise", "valor": "01/01/2020..31/12/2030"}]),
        _d(acao="lembrar", nota="Usar saldo em aberto", tipo_nota="termo", alcance="usuario"),
        _d(acao="preparar_consulta", objetivo="titulos-em-atraso", entrega="calculo", parametros=comum + [
            {"codigo": "data-corte", "valor": "13/11/2026"}, {"codigo": "dias-tolerancia-atraso", "valor": "0"}]),
    ])
    msg, res = conversas.enviar(ana, c, "quanto vamos receber? e quanto está em atraso? guarde: use saldo em aberto", p)
    assert p.chamadas == 3 and res is not None and res.ok, msg.texto
    assert NotaAgente.objects.filter(texto="Usar saldo em aberto").exists()
    assert Objetivo.objects.filter(conversa=c, situacao=SituacaoObjetivo.CUMPRIDO).exists()


def test_falha_ao_guardar_nao_marca_ensinamento_como_feito(ana, monkeypatch):
    from iativa.agent import memoria
    from iativa.agent.models import NotaAgente

    def recusar(*args, **kwargs):
        raise ValidationError("Nota recusada.")

    monkeypatch.setattr(memoria, "lembrar", recusar)
    c = conversas.nova_individual(ana)
    cm, _ = _consulta(ana, c)
    p = FakeProvider([
        _d(acao="preparar_consulta", objetivo=cm.codigo, entrega="calculo",
           parametros=FILIAL + [{"codigo": "agregacao", "valor": "soma:E1_VALOR"}]),
        _d(acao="lembrar", nota="Usar o saldo", tipo_nota="termo", alcance="usuario"),
        _d(resposta="Anotado e concluído."),
    ])
    msg, _ = conversas.enviar(ana, c, "some os títulos e guarde: use o saldo", p)
    assert not NotaAgente.objects.exists()
    assert not Objetivo.objects.filter(conversa=c, situacao=SituacaoObjetivo.CUMPRIDO).exists()
