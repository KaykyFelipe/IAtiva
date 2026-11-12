"""Tarefas longas (M25): investigar a lista inteira em segundo plano, com cobertura verificada.

Base sintética (generator.FUNCIONARIOS), lista "desligados de 01/09 a 12/11/2026, todas as filiais":
ANA (sem outro vínculo; a readmissão dela está excluída), BRUNO (readmitido, função e categoria
diferentes), CARLA (novo vínculo em outra filial no dia seguinte), DANIEL (homônimo com outro
documento), ELISA (só vínculo anterior), FABIO (vínculo simultâneo), IARA e JOAO (sem outro).
"""
import json
import logging
from io import StringIO

import pytest
from django.core.management import call_command
from django.http import QueryDict
from django.urls import reverse

from iativa.accounts.models import AccessPolicy, Department
from iativa.agent import aprendizado, investigacao as inv, prazo, tarefas
from iativa.agent.models import SituacaoTarefa as S, Tarefa
from iativa.agent.provedores import FakeProvider
from iativa.audit.models import OperationAudit
from iativa.chat import conversas
from iativa.chat.models import Mensagem
from iativa.synthetic_erp.generator import FUNCIONARIOS

pytestmark = pytest.mark.django_db

LISTA = {"ANA SINTETICA SOUZA", "BRUNO SINTETICO LIMA", "CARLA SINTETICA ROCHA", "DANIEL SINTETICO MELO",
         "ELISA SINTETICA DIAS", "FABIO SINTETICO REIS", "IARA SINTETICA COSTA", "JOAO SINTETICO VAZ"}
CAMPOS = {"inicio": "RA_ADMISSA", "fim": "RA_DEMISSA", "situacao": "RA_SITFOLH", "cargo": "RA_CODFUNC",
          "categoria": "RA_CATFUNC"}


@pytest.fixture
def base(erp_sintetico, settings, novo_usuario, politica):
    settings.IATIVA_METADADOS = {"fonte": "sintetico"}
    settings.IATIVA_APRENDIZADO_AUTOAPROVACAO = True
    settings.IATIVA_VINCULO_POR_DOCUMENTO = True
    call_command("seed_exemplos", stdout=StringIO())
    cob = Department.objects.get(codigo="financeiro-cobranca")
    admin = novo_usuario("diego", cob)
    politica(admin, cob, consultar=True, administrar=True, aprovar_conhecimento=True, filiais=["01", "02", "03"])
    q = QueryDict(mutable=True)
    q.update({"nome": "Funcionarios demitidos", "campo_data": "RA_DEMISSA"})
    q.setlist("colunas", ["RA_MAT", "RA_NOME", "RA_ADMISSA", "RA_DEMISSA"])
    q.setlist("setores", [str(cob.pk)])
    cm = aprendizado.aprovar(admin, aprendizado.salvar_proposta(admin, "SRA", q))
    return {"u": admin, "cm": cm, "cob": cob}


def _d(**kw):
    padrao = {"resposta": "", "acao": "conversar", "objetivo": "", "parametros": [], "alvo": "", "nota": "",
              "confirmado": False, "opcoes": []}
    return {**padrao, **kw}


def _lista(b, c=None):
    c = c or conversas.nova_individual(b["u"])
    p = FakeProvider([_d(acao="preparar_consulta", objetivo=b["cm"].codigo, parametros=[
        {"codigo": "filial-analise", "valor": "TODAS"}, {"codigo": "periodo-analise", "valor": "01/09/2026..12/11/2026"}])])
    msg, res = conversas.enviar(b["u"], c, "quem foi demitido nos últimos 2 meses?", p)
    assert res is not None and res.ok, msg.texto
    assert {l[2].strip() for l in res.linhas} == LISTA
    return c


PERGUNTA = ("você consegue verificar se algum deles teve o cargo trocado? o BRUNO aparece como demitido mas acho que "
            "foi readmitido. quero confirmar se todos que estão aí foram demitidos mesmo")


def _investigar(b, c, campos=CAMPOS, identidade="documento", antes=()):
    p = FakeProvider([*antes, _d(acao="investigar_lista", investigacao={"campos": campos, "identidade": identidade})])
    msg, res = conversas.enviar(b["u"], c, PERGUNTA, p)
    return msg, p


def _tarefa(b, **kw):
    c = _lista(b)
    msg, _ = _investigar(b, c, **kw)
    return Tarefa.objects.get(pk=msg.ficha["tarefa"]), c


def _itens(concl):
    from types import SimpleNamespace as N

    return [N(reg=N(nome=i["nome"], chave=i["chave"]), **i) for i in concl["itens"]]


def _por_nome(concl):
    out = {}
    for it in _itens(concl):
        out.setdefault(it.reg.nome, []).append(it)
    return out


# --- escopo e cobertura ---------------------------------------------------------------------------

def test_exemplo_nao_reduz_o_escopo_e_vira_tarefa_da_lista_inteira(base):
    c = _lista(base)
    exemplo = _d(acao="preparar_consulta", objetivo=base["cm"].codigo,
                 parametros=[{"codigo": "texto-busca", "valor": "BRUNO SINTETICO LIMA"}])
    adiar = _d(resposta="Consultei o Bruno. Depois dá para repetir com os outros.")
    msg, p = _investigar(base, c, antes=(exemplo, adiar))
    assert p.chamadas == 3 and msg.ficha["tipo"] == "tarefa"  # o exemplo e o "depois" foram devolvidos ao modelo
    assert not Mensagem.objects.filter(texto__icontains="depois dá para repetir").exists()
    t = Tarefa.objects.get()
    assert t.situacao == S.PENDENTE and t.plano["identidade"] == "documento"
    assert tarefas.processar_proxima()
    t.refresh_from_db()
    assert t.situacao == S.CONCLUIDA and t.cobertura["total"] == len(LISTA)
    assert t.cobertura["verificados"] + t.cobertura["inconclusivos"] == len(LISTA)
    assert all(e["estado"] == "concluida" for e in t.etapas)


def test_classificacao_por_documento(base):
    t, _ = _tarefa(base)
    tarefas.processar_proxima()
    t.refresh_from_db()
    pessoas = _por_nome(tarefas.conclusao(base["u"], t))
    assert set(pessoas) == LISTA and all(len(v) == 1 for v in pessoas.values())
    p = {k: v[0] for k, v in pessoas.items()}
    assert p["ANA SINTETICA SOUZA"].classe == "encerrado_sem_outro"  # a readmissão excluída não conta
    bruno = p["BRUNO SINTETICO LIMA"]
    assert bruno.classe == "novo_vinculo_posterior" and "02/10/2026" in bruno.texto and "00004 → 00175" in bruno.texto
    assert "não comprova" in bruno.texto and "efetivado" not in bruno.texto.lower()
    assert "transferência" in p["CARLA SINTETICA ROCHA"].texto
    assert "transferência" not in bruno.texto  # mesma filial
    assert p["DANIEL SINTETICO MELO"].classe == "encerrado_sem_outro"  # homônimo com outro documento
    assert p["ELISA SINTETICA DIAS"].classe == "so_vinculo_anterior"
    assert p["FABIO SINTETICO REIS"].classe == "vinculo_simultaneo"
    assert all(x.verificado for x in p.values())


def test_homonimo_nao_e_vinculado_quando_a_comparacao_e_por_nome(base, settings):
    settings.IATIVA_VINCULO_POR_DOCUMENTO = False
    t, _ = _tarefa(base, identidade="documento")  # pediu documento, mas não está autorizado
    assert t.plano["identidade"] == "nome" and "não está autorizada" in t.plano["aviso"]
    tarefas.processar_proxima()
    t.refresh_from_db()
    assert t.situacao == S.CONCLUIDA
    p = {k: v[0] for k, v in _por_nome(tarefas.conclusao(base["u"], t)).items()}
    daniel, bruno = p["DANIEL SINTETICO MELO"], p["BRUNO SINTETICO LIMA"]
    assert daniel.verificado is False and daniel.classe == "mesmo_nome" and "mesma pessoa" in daniel.texto
    assert bruno.verificado is False and bruno.classe == "mesmo_nome"  # sem documento, nem o readmitido é afirmado
    assert p["IARA SINTETICA COSTA"].verificado and t.cobertura["inconclusivos"] >= 5


# --- recuperação, limites, permissão, cancelamento ------------------------------------------------

def test_campo_errado_e_corrigido_na_mesma_mensagem(base):
    c = _lista(base)
    errado = _d(acao="investigar_lista", investigacao={"campos": {**CAMPOS, "inicio": "RA_NOME"}})
    msg, p = _investigar(base, c, antes=(errado,))
    assert p.chamadas == 2 and msg.ficha["tipo"] == "tarefa"


def test_plano_invalido_no_worker_e_replanejado_pelo_modelo(base, monkeypatch):
    t, _ = _tarefa(base)
    Tarefa.objects.filter(pk=t.pk).update(proposta={"campos": {**CAMPOS, "fim": "RA_INEXISTE"}})
    p = FakeProvider([_d(acao="investigar_lista", investigacao={"campos": CAMPOS, "identidade": "documento"})])
    monkeypatch.setattr(tarefas, "obter_provedor", lambda usuario=None: p)
    tarefas.processar_proxima()
    t.refresh_from_db()
    assert t.situacao == S.CONCLUIDA and t.consumo["chamadas_modelo"] == 1 and p.chamadas == 1


def test_lista_maior_que_o_limite_nao_e_concluida(base, settings):
    t, _ = _tarefa(base)
    Tarefa.objects.filter(pk=t.pk).update(limites={**t.limites, "registros": 3})
    tarefas.processar_proxima()
    t.refresh_from_db()
    assert t.situacao == S.LIMITE and "limite" in t.motivo and t.cobertura["total"] == 3
    assert not t.cobertura["lista_completa"]
    assert "Parei antes de terminar" in Mensagem.objects.latest("id").texto


def test_lote_cortado_deixa_itens_inconclusivos_com_motivo(base):
    _lista(base)
    ctx = inv.Contexto()
    plano = inv.validar_plano(base["u"], _base_sol(base), {"campos": CAMPOS, "identidade": "documento"})
    ctl = inv.Controle({"registros": 100, "lote": 40, "consultas": 50})
    inv.reconstruir_lista(base["u"], _base_sol(base), plano, ctx, ctl)
    ctl.limites = {**ctl.limites, "registros": 1}  # cada lote só devolve 1 linha: cortado
    inv.buscar_relacionados(base["u"], _base_sol(base), plano, ctx, ctl)
    inv.classificar(plano, ctx)
    cob = inv.cobertura(ctx)
    assert cob["sem_classificacao"] == 0 and cob["inconclusivos"] == len(LISTA)
    assert all("limite" in it.texto for it in ctx.itens)


def _base_sol(b):
    from iativa.chat.models import RequestContext

    return RequestContext.objects.filter(objetivo=b["cm"].codigo, estado="concluida").latest("id")


def test_lotes_evitam_uma_consulta_por_pessoa(base):
    t, _ = _tarefa(base)
    tarefas.processar_proxima()
    t.refresh_from_db()
    assert t.consumo["consultas"] <= 3 + 3  # lista (3 filiais) + 1 lote por filial


def test_permissao_retirada_durante_a_execucao_bloqueia(base, monkeypatch):
    t, _ = _tarefa(base)
    original = inv.buscar_relacionados

    def sem_permissao(*a, **kw):
        AccessPolicy.objects.filter(user=base["u"]).update(pode_consultar=False)
        return original(*a, **kw)

    monkeypatch.setattr(inv, "buscar_relacionados", sem_permissao)
    tarefas.processar_proxima()
    t.refresh_from_db()
    assert t.situacao == S.BLOQUEADA and "permissão" in t.motivo


def test_cancelamento_impede_novas_operacoes(base, monkeypatch):
    t, _ = _tarefa(base)
    feitas = {"n": 0}
    original = inv._consultar

    def contar(*a, **kw):
        feitas["n"] += 1
        if feitas["n"] == 1:
            tarefas.cancelar(base["u"], t.pk)  # a pessoa cancela durante a primeira consulta
        return original(*a, **kw)

    monkeypatch.setattr(inv, "_consultar", contar)
    tarefas.processar_proxima()
    t.refresh_from_db()
    assert t.situacao == S.CANCELADA and feitas["n"] == 1


def test_cancelar_na_fila(base):
    t, _ = _tarefa(base)
    tarefas.cancelar(base["u"], t.pk)
    assert not tarefas.processar_proxima()
    t.refresh_from_db()
    assert t.situacao == S.CANCELADA


def test_prazo_total_respeitado(base, monkeypatch):
    t, _ = _tarefa(base)
    relogio = {"t": 0.0}
    monkeypatch.setattr(prazo, "agora", lambda: relogio["t"])
    original = inv._consultar

    def lenta(*a, **kw):
        relogio["t"] += 400  # cada consulta leva 400 s
        return original(*a, **kw)

    monkeypatch.setattr(inv, "_consultar", lenta)
    Tarefa.objects.filter(pk=t.pk).update(limites={**t.limites, "prazo_s": 500})
    tarefas.processar_proxima()
    t.refresh_from_db()
    assert t.situacao == S.LIMITE and t.consumo["consultas"] == 1  # o prazo venceu: a segunda nem começou


# --- worker: página fechada, reinício, concorrência -----------------------------------------------

def test_fechar_a_pagina_nao_interrompe(base, client):
    c = _lista(base)
    client.force_login(base["u"])
    from iativa.agent import agente

    p = FakeProvider([_d(acao="investigar_lista", investigacao={"campos": CAMPOS, "identidade": "documento"})])
    import iativa.agent.agente as ag

    ag_obter = ag.obter_provedor
    ag.obter_provedor = lambda usuario=None: p
    try:
        r = client.post(reverse("chat:mensagem", args=[c.pk]), {"mensagem": PERGUNTA})
    finally:
        ag.obter_provedor = ag_obter
    t = Tarefa.objects.get()
    assert r.status_code == 200 and f"tarefa-{t.pk}" in r.content.decode()
    client.logout()  # a página fechou
    assert tarefas.processar_proxima()
    t.refresh_from_db()
    assert t.situacao == S.CONCLUIDA
    client.force_login(base["u"])
    estado = client.get(reverse("chat:tarefa", args=[t.pk])).content.decode()
    assert "Concluída" in estado and "SINTETIC" not in estado
    html = client.get(reverse("chat:tarefa_conclusao", args=[t.pk])).content.decode()
    assert all(n in html for n in LISTA) and "90000000" not in html  # documento nunca aparece
    assert agente is not None


def test_reinicio_do_worker_reconstroi_sem_duplicar(base):
    t, _ = _tarefa(base)
    primeira = tarefas.reivindicar()
    assert primeira.pk == t.pk and tarefas.reivindicar() is None  # lease válido: ninguém mais pega
    # O worker morreu depois da lista: etapa concluída, evidências perdidas, lease vencido.
    etapas = [dict(e, estado="concluida") if e["id"] in ("permissoes", "lista") else e for e in primeira.etapas]
    from django.utils import timezone
    from datetime import timedelta

    Tarefa.objects.filter(pk=t.pk).update(etapas=etapas, lease_ate=timezone.now() - timedelta(seconds=1))
    segunda = tarefas.reivindicar()
    assert segunda.execucao != primeira.execucao and segunda.execucoes == 2
    # A execução antiga, se voltar, não grava mais nada.
    ctl_antigo = tarefas._Controle(primeira)
    with pytest.raises(tarefas.PerdeuExecucao):
        ctl_antigo.checar()
    tarefas.executar(segunda)
    segunda.refresh_from_db()
    assert segunda.situacao == S.CONCLUIDA and segunda.reconstruida
    assert segunda.cobertura["total"] == len(LISTA)
    assert Mensagem.objects.filter(ficha__tipo="tarefa_fim").count() == 1


# --- privacidade ----------------------------------------------------------------------------------

def test_dados_pessoais_fora_dos_armazenamentos(base, caplog):
    caplog.set_level(logging.DEBUG)
    t, _ = _tarefa(base)
    tarefas.processar_proxima()
    t.refresh_from_db()
    tarefas.conclusao(base["u"], t)
    t.refresh_from_db()
    # M29: a conclusão (nomes, matrículas, textos) fica só no campo `conclusao` da tarefa; o CPF nunca.
    cpfs = [f[3] for f in FUNCIONARIOS]
    assert t.conclusao["itens"] and not [x for x in cpfs if x in json.dumps(t.conclusao)]
    proibidos = [n for n in LISTA if n.split()[0] != "BRUNO"] + cpfs + ["20261001", "01/10/2026", "00175"]
    gravado = json.dumps({k: v for k, v in t.__dict__.items() if not k.startswith("_") and k != "conclusao"},
                         default=str)
    gravado += " ".join(m.texto + json.dumps(m.ficha) for m in Mensagem.objects.filter(papel="ia"))
    gravado += " ".join(str(a.__dict__) for a in OperationAudit.objects.all()) + caplog.text
    assert not [x for x in proibidos if x in gravado]
