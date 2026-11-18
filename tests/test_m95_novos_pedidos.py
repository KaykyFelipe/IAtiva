"""M95: pedidos NOVOS (sem receita pré-cadastrada) em RH, Comercial e Financeiro, com dados sintéticos.

Para cada área: descoberta (memória técnica + dicionário local), criação da receita, execução, Markdown da receita e
reaproveitamento depois de "reiniciar" (caches zerados, outra conversa, outra redação e outros parâmetros), com a
mesma receita (mesma assinatura: o código é o mesmo). As barreiras de acesso são as mesmas nos três setores.
"""
from io import StringIO

import pytest
from django.core.management import call_command

from iativa.accounts.models import AccessPolicy, Department
from iativa.agent import receitas
from iativa.agent.contratos import objetivos_para
from iativa.agent.models import ConsultaModelo
from iativa.chat import conversas
from iativa.knowledge import memoria_tecnica as mt

from .test_objetivos import Espiao

pytestmark = pytest.mark.django_db(transaction=True)

# (setor, pedido novo, reformulação, tabela, colunas, campo de data)
AREAS = {
    "rh": ("Quem foram os funcionários desligados no período?", "e os demitidos do ano passado inteiro?",
           "SRA", ["RA_MAT", "RA_NOME", "RA_DEMISSA"], "RA_DEMISSA"),
    "comercial": ("Quais clientes estão bloqueados?", "liste de novo os clientes com bloqueio, todas as filiais",
                  "SA1", ["A1_COD", "A1_NOME", "A1_MSBLQL"], ""),
    "financeiro-cobranca": ("Quais movimentos bancários tivemos no mês?", "e os movimentos do mês anterior?",
                            "SE5", ["E5_DATA", "E5_TIPODOC", "E5_VALOR"], "E5_DATA"),
}


def _d(**kw):
    base = {"resposta": "", "acao": "conversar", "objetivo": "", "parametros": [], "alvo": "", "nota": "",
            "confirmado": False, "opcoes": []}
    return {**base, **kw}


@pytest.fixture(autouse=True)
def _ambiente(erp_sintetico, settings, tmp_path):
    settings.IATIVA_METADADOS = {"fonte": "sintetico"}
    settings.IATIVA_RECEITAS_DIR = tmp_path / "receitas"
    call_command("seed_exemplos", stdout=StringIO())
    for codigo, nome in (("rh", "Recursos Humanos"), ("comercial", "Comercial")):
        Department.objects.get_or_create(codigo=codigo, defaults={"nome": nome})


def _usuario(novo_usuario, politica, setor):
    dep = Department.objects.get(codigo=setor)
    u = novo_usuario(f"ti-{setor}", dep)
    politica(u, dep, consultar=True, filiais=["01"])
    politica(u, Department.objects.get(codigo="ti"), administrar=True)  # TI aprova a fonte (M81)
    return u


def _reiniciar():
    mt._CACHE.update(assinatura=None, sha="", caminho=None)


def _primeiro(u, setor):
    pedido, _, tabela, colunas, campo_data = AREAS[setor]
    params = [{"codigo": "filial-analise", "valor": "01"}]
    if campo_data:
        params.append({"codigo": "periodo-analise", "valor": "01/01/2000..31/12/2030"})
    p = Espiao([_d(acao="aprender_tabela", alvo=pedido),
                _d(acao="criar_consulta", parametros=params,
                   consulta={"tabela": tabela, "nome": f"Receita {setor}", "colunas": colunas, "campo_data": campo_data})])
    msg, res = conversas.enviar(u, conversas.nova_individual(u), pedido, p)
    return msg, res, p


@pytest.mark.parametrize("setor", list(AREAS))
def test_pedido_novo_descobre_executa_grava_receita_e_reaproveita(setor, novo_usuario, politica):
    u = _usuario(novo_usuario, politica, setor)
    assert not ConsultaModelo.objects.exists()  # nenhuma receita pré-cadastrada
    msg, res, p = _primeiro(u, setor)
    assert res is not None and res.ok, msg.texto
    cm = ConsultaModelo.objects.get()
    tabela = AREAS[setor][2]
    # Descoberta: a memória técnica foi consultada (ou a ausência dela registrada) e o modelo recebeu trechos mínimos.
    entrada = next(e for e in p.entradas if e.tabelas_para_aprender)
    t = next(x for x in entrada.tabelas_para_aprender if x["tabela"] == tabela)
    assert t["memoria_tecnica"]["estado"] == mt.OK and len(t["referencia_publica"]) <= 4
    # Execução: busca obrigatória registrada no resultado, pela versão do índice.
    assert res.memoria_tecnica["estado"] == mt.OK and res.memoria_tecnica["versao"] == mt.indice()[2][:12]
    assert tabela in res.memoria_tecnica["aliases"]
    # Receita em Markdown, sem dados do ERP.
    md = (receitas.pasta() / cm.codigo / "atual.md").read_text(encoding="utf-8")
    assert f"Tabela principal: `{tabela}`" in md and "R$" not in md
    assinatura = receitas.assinatura(cm)

    _reiniciar()
    _, reformulado, _, _, campo_data = AREAS[setor]
    params = [{"codigo": "filial-analise", "valor": "TODAS"}]
    if campo_data:
        params.append({"codigo": "periodo-analise", "valor": "01/01/2010..31/12/2025"})
    p2 = Espiao([_d(acao="preparar_consulta", objetivo=cm.codigo, parametros=params)])
    msg2, res2 = conversas.enviar(u, conversas.nova_individual(u), reformulado, p2)
    assert res2 is not None and res2.ok, msg2.texto
    assert ConsultaModelo.objects.count() == 1 and receitas.assinatura(ConsultaModelo.objects.get()) == assinatura
    assert res2.memoria_tecnica["estado"] == mt.OK


def test_tabela_local_fora_da_referencia_publica_segue_pelo_dicionario_local(novo_usuario, politica):
    # SE5 existe no dicionário local sintético e não está no Markdown sintético.
    u = _usuario(novo_usuario, politica, "financeiro-cobranca")
    _, res, p = _primeiro(u, "financeiro-cobranca")
    t = next(x for x in next(e for e in p.entradas if e.tabelas_para_aprender).tabelas_para_aprender
             if x["tabela"] == "SE5")
    assert any(r["secao"] == "ausente" for r in t["referencia_publica"]) and res.ok


def test_rh_nao_usa_receita_do_comercial_e_revogacao_vale_na_hora(novo_usuario, politica):
    u = _usuario(novo_usuario, politica, "comercial")
    _primeiro(u, "comercial")
    cm = ConsultaModelo.objects.get()
    rh = novo_usuario("so-rh", Department.objects.get(codigo="rh"))
    politica(rh, Department.objects.get(codigo="rh"), consultar=True, filiais=["01"])
    assert cm.codigo not in {o.codigo for o in objetivos_para(rh)}
    # Revogação depois de aprender: a mesma pessoa perde o acesso ao setor e a receita some do catálogo dela.
    AccessPolicy.objects.filter(user=u, department__codigo="comercial").delete()
    assert cm.codigo not in {o.codigo for o in objetivos_para(u)}


def test_indice_indisponivel_executa_pelo_dicionario_local_e_diz_isso(novo_usuario, politica, settings, tmp_path):
    u = _usuario(novo_usuario, politica, "rh")
    settings.IATIVA_MEMORIA_TECNICA = tmp_path / "ausente.md"
    _reiniciar()
    msg, res, _ = _primeiro(u, "rh")
    assert res is not None and res.ok, msg.texto
    assert res.memoria_tecnica["estado"] == mt.SEM_ARQUIVO
    assert any("Memória técnica indisponível" in x for x in res.premissas)


def test_worker_tambem_consulta_a_memoria(novo_usuario, politica, monkeypatch):
    """A continuação no worker executa pelo mesmo serviço: a busca obrigatória acontece lá também."""
    from iativa.agent import agente, objetivos as coord
    from iativa.agent.models import Objetivo, SituacaoObjetivo as SO
    from iativa.chat.models import Mensagem
    from iativa.query import servico
    u = _usuario(novo_usuario, politica, "rh")
    _primeiro(u, "rh")
    cm = ConsultaModelo.objects.get()
    c = conversas.nova_individual(u)
    m = Mensagem.objects.create(conversa=c, papel="usuario", autor=u, texto="Quem foram os desligados?")
    Objetivo.objects.create(usuario=u, conversa=c, mensagem=m, exigencia="consulta", origem="acao",
                            situacao=SO.CONTINUAR, contrato={"entregas": [], "cumpridas": []})
    chamadas = []
    original = servico.memoria_tecnica_da_solicitacao

    def espiar(s):
        r = original(s)
        chamadas.append(r)
        return r
    monkeypatch.setattr(servico, "memoria_tecnica_da_solicitacao", espiar)
    monkeypatch.setattr(agente, "obter_provedor", lambda usuario=None: Espiao([_d(
        acao="preparar_consulta", objetivo=cm.codigo, parametros=[{"codigo": "filial-analise", "valor": "01"},
                                                                  {"codigo": "periodo-analise", "valor": "01/01/2000..31/12/2030"}])]))
    assert coord.continuar_proximo()
    assert chamadas and chamadas[0]["estado"] == mt.OK and "SRA" in chamadas[0]["aliases"]


def test_metadados_desatualizados_e_nenhuma_linha(novo_usuario, politica):
    u = _usuario(novo_usuario, politica, "rh")
    _primeiro(u, "rh")
    cm = ConsultaModelo.objects.get()
    # Nenhuma linha: período sem registros é resposta válida (zero), não troca de fonte.
    p = Espiao([_d(acao="preparar_consulta", objetivo=cm.codigo, parametros=[
        {"codigo": "filial-analise", "valor": "01"}, {"codigo": "periodo-analise", "valor": "01/01/1990..31/12/1990"}])])
    msg, res = conversas.enviar(u, conversas.nova_individual(u), "desligados em 1990?", p)
    assert res is not None and res.ok and res.linhas == []
    # Metadados desatualizados: campo da receita que não existe mais no dicionário local é recusado, sem executar.
    spec = dict(cm.spec)
    spec["colunas"] = spec["colunas"] + [{"campo": "RA_XREMOVIDO", "rotulo": "Removido", "tipo": "texto"}]
    cm.spec = spec
    cm.save(update_fields=["spec"])
    p = Espiao([_d(acao="preparar_consulta", objetivo=cm.codigo, parametros=[
        {"codigo": "filial-analise", "valor": "01"}, {"codigo": "periodo-analise", "valor": "01/01/2000..31/12/2030"}])])
    msg, res = conversas.enviar(u, conversas.nova_individual(u), "desligados?", p)
    assert res is None or not res.ok
    from iativa.chat.models import RequestContext
    assert RequestContext.objects.filter(usuario=u).order_by("-pk").first().estado in ("falhou", "nao_validada")
