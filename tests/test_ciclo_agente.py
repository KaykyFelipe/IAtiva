"""Ciclo fechado do agente (M22): observar a execução, corrigir falha recuperável, parar no limite,
não contornar permissão, não confiar em números do navegador e aprender com correções válidas."""
import dataclasses
import json
import sqlite3
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.http import QueryDict
from django.urls import reverse
from django.utils import timezone

from iativa.accounts.models import Department
from iativa.agent import agente, aprendizado, memoria
from iativa.agent.models import NotaAgente, TipoNota
from iativa.agent.provedores import FakeProvider
from iativa.chat import conversas
from iativa.chat.models import Mensagem
from iativa.query import servico
from iativa.query.executor import TempoEsgotado

pytestmark = pytest.mark.django_db


@pytest.fixture
def base(erp_sintetico, settings, novo_usuario, politica):
    settings.IATIVA_METADADOS = {"fonte": "sintetico"}
    settings.IATIVA_APRENDIZADO_AUTOAPROVACAO = True
    call_command("seed_exemplos", stdout=StringIO())
    cob = Department.objects.get(codigo="financeiro-cobranca")
    ti = Department.objects.get(codigo="ti")
    admin = novo_usuario("diego", cob)
    politica(admin, cob, consultar=True, administrar=True, aprovar_conhecimento=True, filiais=["01", "02"])
    q = QueryDict(mutable=True)
    q.update({"nome": "Titulos", "campo_data": ""})
    q.setlist("colunas", ["E1_NUM", "E1_TIPO", "E1_CLIENTE", "E1_VALOR"])
    q.setlist("setores", [str(cob.pk), str(ti.pk)])
    cm = aprendizado.aprovar(admin, aprendizado.salvar_proposta(admin, "SE1", q))
    ana = novo_usuario("ana", cob)
    politica(ana, cob, consultar=True, filiais=["01", "02"])
    bia = novo_usuario("bia", cob)
    politica(bia, cob, consultar=True, filiais=["01", "02"])
    caio = novo_usuario("caio", ti)
    politica(caio, ti, consultar=True, filiais=["01", "02"])
    with sqlite3.connect(erp_sintetico) as conn:  # garante um NCC e um RA vivos na filial 02
        ids = [r[0] for r in conn.execute("SELECT R_E_C_N_O_ FROM SE1010 WHERE E1_FILIAL = '02' AND D_E_L_E_T_ = ' ' "
                                          "ORDER BY R_E_C_N_O_ LIMIT 2")]
        conn.execute("UPDATE SE1010 SET E1_TIPO = 'NCC' WHERE R_E_C_N_O_ = ?", (ids[0],))
        conn.execute("UPDATE SE1010 SET E1_TIPO = 'RA ' WHERE R_E_C_N_O_ = ?", (ids[1],))
    return {"admin": admin, "ana": ana, "bia": bia, "caio": caio, "cm": cm, "erp": erp_sintetico, "cob": cob}


def _d(**kw):
    padrao = {"resposta": "", "acao": "conversar", "objetivo": "", "parametros": [], "alvo": "", "nota": "",
              "confirmado": False, "opcoes": []}
    return {**padrao, **kw}


def _consulta(cm, filial="01", agregacao=""):
    params = [{"codigo": "filial-analise", "valor": filial}]
    if agregacao:
        params.append({"codigo": "agregacao", "valor": agregacao})
    return _d(acao="preparar_consulta", objetivo=cm.codigo, parametros=params)


def _contar(erp, filial, extra=""):
    with sqlite3.connect(erp) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM SE1010 WHERE E1_FILIAL = ? AND D_E_L_E_T_ = ' ' {extra}",
                            (filial,)).fetchone()[0]


class Espiao(FakeProvider):
    def __init__(self, respostas):
        super().__init__(respostas)
        self.entradas = []

    def decidir(self, entrada):
        self.entradas.append(entrada)
        return super().decidir(entrada)


# --- recuperação ----------------------------------------------------------------------------------

def test_falha_recuperavel_e_corrigida_na_mesma_mensagem(base):
    c = conversas.nova_individual(base["ana"])
    p = Espiao([_consulta(base["cm"], agregacao="soma:E1_CLIENTE"),  # campo de texto: cálculo inválido
                _consulta(base["cm"], agregacao="soma:E1_VALOR")])
    msg, res = conversas.enviar(base["ana"], c, "some os títulos", p)
    assert res is not None and res.ok and res.totais.registros == _contar(base["erp"], "01"), msg.texto
    assert p.chamadas == 2
    obs = p.entradas[1].observacoes
    assert obs[0]["situacao"] == agente.ERRO_PARAMETROS and "numérico" in obs[0]["motivo"]
    assert not p.entradas[0].observacoes


def test_para_no_limite_de_tentativas(base, settings):
    settings.IATIVA_AGENTE_CICLO = {"passos": 3, "retentativas": 0, "tempo_s": 60}
    c = conversas.nova_individual(base["ana"])
    p = Espiao([_consulta(base["cm"], agregacao=f"soma:XX_NADA{i}") for i in range(5)])
    msg, res = conversas.enviar(base["ana"], c, "some", p)
    assert res is None and p.chamadas == 3 and len(p.respostas) == 2
    assert "Tentei 3 caminhos" in msg.texto and "limite de tentativas" in msg.texto
    assert msg.ficha["tentativas"] == 3


def test_nao_repete_a_mesma_acao_que_ja_falhou(base):
    c = conversas.nova_individual(base["ana"])
    igual = _consulta(base["cm"], agregacao="soma:XX_NADA")
    p = Espiao([igual, dict(igual), _consulta(base["cm"], agregacao="soma:E1_VALOR")])
    msg, res = conversas.enviar(base["ana"], c, "some", p)
    assert res is None and p.chamadas == 2 and len(p.respostas) == 1  # parou sem executar a terceira
    assert "Cálculo inválido" in msg.texto


def test_sem_permissao_nao_e_contornada(base):
    c = conversas.nova_individual(base["ana"])
    p = Espiao([_consulta(base["cm"], filial="03"), _consulta(base["cm"], filial="01")])
    msg, res = conversas.enviar(base["ana"], c, "títulos da filial 03", p)
    assert res is None and p.chamadas == 1 and len(p.respostas) == 1  # não trocou por outra filial
    assert "filiais autorizadas" in msg.texto


def test_resultado_vazio_nao_amplia_o_escopo(base):
    with sqlite3.connect(base["erp"]) as conn:
        conn.execute("UPDATE SE1010 SET D_E_L_E_T_ = '*', R_E_C_D_E_L_ = R_E_C_N_O_ WHERE E1_FILIAL = '01'")
    c = conversas.nova_individual(base["ana"])
    p = Espiao([_consulta(base["cm"], filial="01"), _consulta(base["cm"], filial="TODAS")])
    msg, res = conversas.enviar(base["ana"], c, "títulos da filial 01", p)
    assert res.ok and res.situacao == servico.VAZIO and res.linhas == []
    assert p.chamadas == 1 and len(p.respostas) == 1 and "Nenhum registro" in res.mensagem


def test_erro_transitorio_e_repetido_dentro_do_limite(base, monkeypatch):
    original, chamadas = servico.executar, {"n": 0}

    def instavel(*a, **kw):
        chamadas["n"] += 1
        if chamadas["n"] == 1:
            raise TempoEsgotado("Tempo limite da consulta.")
        return original(*a, **kw)

    monkeypatch.setattr(servico, "executar", instavel)
    c = conversas.nova_individual(base["ana"])
    p = Espiao([_consulta(base["cm"])])
    msg, res = conversas.enviar(base["ana"], c, "títulos", p)
    assert res is not None and res.ok and p.chamadas == 1, msg.texto


def test_erro_transitorio_persistente_encerra_com_motivo(base, monkeypatch):
    def sempre(*a, **kw):
        raise TempoEsgotado("Tempo limite da consulta.")

    monkeypatch.setattr(servico, "executar", sempre)
    c = conversas.nova_individual(base["ana"])
    p = Espiao([_consulta(base["cm"]), _consulta(base["cm"], filial="02")])
    msg, res = conversas.enviar(base["ana"], c, "títulos", p)
    assert res is None and p.chamadas == 1  # tempo esgotado não devolve a decisão ao modelo
    assert "Tentei 2 vezes" in msg.texto and "TempoEsgotado" in msg.texto


# --- navegador ------------------------------------------------------------------------------------

def test_resumo_adulterado_no_navegador_nao_chega_ao_modelo(base, client, monkeypatch):
    espiao = Espiao([_d(resposta="Vou calcular de novo.")])
    monkeypatch.setattr(agente, "obter_provedor", lambda usuario=None: espiao)
    client.force_login(base["ana"])
    c = conversas.nova_individual(base["ana"])
    falso = "Totais: Vlr.Titulo 999.999.999,99"
    r = client.post(reverse("chat:mensagem", args=[c.pk]),
                    {"mensagem": "quanto deu?", "resultados": json.dumps([f"{base['cm'].codigo}: {falso}"])})
    assert r.status_code == 200
    visto = json.dumps(dataclasses.asdict(espiao.entradas[0]), ensure_ascii=False, default=str)
    assert "999.999.999" not in visto and not hasattr(espiao.entradas[0], "resultados_na_tela")
    assert "999.999.999" not in r.content.decode()
    assert not Mensagem.objects.filter(texto__contains="999.999.999").exists()


# --- memória e correções --------------------------------------------------------------------------

REGRA = {"tabela": "SE1", "campo": "E1_TIPO", "operador": "diferente", "valor": "NCC"}
FRASE = "quando eu falar títulos, não conte as notas de crédito"


def _ensinar(user, confirmado, alcance="", regra=REGRA):
    c = conversas.nova_individual(user)
    p = FakeProvider([_d(acao="lembrar", nota=FRASE, tipo_nota="regra", regra=regra, confirmado=confirmado,
                         alcance=alcance)])
    return conversas.enviar(user, c, FRASE, p)[0]


def _linhas(user, cm, filial="02"):
    c = conversas.nova_individual(user)
    msg, res = conversas.enviar(user, c, "títulos", FakeProvider([_consulta(cm, filial=filial)]))
    assert res is not None and res.ok, msg.texto
    return res


def test_regra_so_vale_depois_de_confirmada_e_vale_em_outra_conversa(base):
    ana, cm, erp = base["ana"], base["cm"], base["erp"]
    todos, sem_ncc = _contar(erp, "02"), _contar(erp, "02", "AND TRIM(E1_TIPO) <> 'NCC'")
    assert sem_ncc < todos

    msg = _ensinar(ana, confirmado=False)
    nota = NotaAgente.objects.get()
    assert nota.tipo == TipoNota.HIPOTESE and nota.escopo == "usuario" and "Confirma?" in msg.texto
    assert len(_linhas(ana, cm).linhas) == todos  # hipótese não filtra nada

    msg = _ensinar(ana, confirmado=True)
    regra = NotaAgente.objects.get(ativo=True)
    assert regra.tipo == TipoNota.REGRA and regra.criterios == {"tabela": "SE1", "campo": "E1_TIPO", "op": "<>",
                                                                "valor": "NCC"}
    res = _linhas(ana, cm)  # conversa nova
    assert len(res.linhas) == sem_ncc and any("Regra da memória (sua)" in p for p in res.premissas)
    # O total calculado no banco também respeita a regra.
    c = conversas.nova_individual(ana)
    _, res = conversas.enviar(ana, c, "quantos?", FakeProvider([_consulta(cm, "02", "contagem:*")]))
    assert res.totais.registros == sem_ncc


def test_memorias_isoladas_entre_usuarios_e_setores(base):
    ana, bia, caio, admin, cm, erp = (base[k] for k in ("ana", "bia", "caio", "admin", "cm", "erp"))
    todos, sem_ncc = _contar(erp, "02"), _contar(erp, "02", "AND TRIM(E1_TIPO) <> 'NCC'")
    _ensinar(ana, confirmado=True)  # regra só da Ana
    assert len(_linhas(ana, cm).linhas) == sem_ncc
    assert len(_linhas(bia, cm).linhas) == todos and len(_linhas(caio, cm).linhas) == todos
    assert all(FRASE not in t for t in agente._notas(bia, FRASE) + agente._notas(caio, FRASE))

    # Regra do setor: proposta até alguém com "aprovar definições" aprovar; depois vale só no setor.
    regra_setor = {**REGRA, "valor": "RA"}
    _ensinar(bia, confirmado=True, alcance="setor", regra=regra_setor)
    proposta = NotaAgente.objects.get(escopo="setor")
    assert proposta.situacao == "proposta" and len(_linhas(bia, cm).linhas) == todos
    memoria.aprovar_nota(admin, proposta)
    sem_ra = _contar(erp, "02", "AND TRIM(E1_TIPO) <> 'RA'")
    assert len(_linhas(bia, cm).linhas) == sem_ra
    assert len(_linhas(caio, cm).linhas) == todos  # outro setor
    assert len(_linhas(ana, cm).linhas) == sem_ncc  # a regra da própria Ana tem precedência no mesmo campo


def test_regra_sem_mapeamento_fica_como_hipotese_e_pede_esclarecimento(base):
    ana, cm, erp = base["ana"], base["cm"], base["erp"]
    c = conversas.nova_individual(ana)
    frase = "quando eu falar faturamento, considere só documentos válidos e exclua os cancelados"
    msg, _ = conversas.enviar(ana, c, frase, FakeProvider([_d(acao="lembrar", nota=frase, tipo_nota="regra")]))
    nota = NotaAgente.objects.get()
    assert nota.tipo == TipoNota.HIPOTESE and nota.criterios == {}
    assert "hipótese" in msg.texto and "qual campo" in msg.texto
    assert len(_linhas(ana, cm).linhas) == _contar(erp, "02")
    assert any("NÃO confirmada" in t for t in agente._notas(ana, "qual o faturamento?"))


def test_regra_com_campo_inexistente_e_recusada(base):
    msg = _ensinar(base["ana"], confirmado=True, regra={**REGRA, "campo": "E1_INVENTADO"})
    assert "não existe" in msg.texto and not NotaAgente.objects.exists()


def test_correcao_pontual_nao_vira_memoria(base):
    ana, cm = base["ana"], base["cm"]
    c = conversas.nova_individual(ana)
    conversas.enviar(ana, c, "títulos", FakeProvider([_consulta(cm, filial="01")]))
    msg, res = conversas.enviar(ana, c, "não, era a filial 02", FakeProvider([_consulta(cm, filial="02")]))
    assert res.ok and any("02" in str(x["valor"]) for x in msg.ficha["criterios"])
    assert not NotaAgente.objects.exists()


def test_recuperacao_por_relevancia_respeita_limite_validade_e_escopo(base):
    ana, bia = base["ana"], base["bia"]
    for i in range(20):
        memoria.lembrar(ana, f"assunto{i:02d} genérico número {i}", "usuario", tipo="termo")
    alvo = memoria.lembrar(ana, "inadimplente é atraso acima de 5 dias", "usuario", tipo="termo")
    vencida = memoria.lembrar(ana, "promoção relâmpago vale para inadimplente", "usuario", tipo="correcao",
                              valido_ate=timezone.localdate() - timedelta(days=1))
    memoria.lembrar(bia, "inadimplente para a Bia é outra coisa", "usuario", tipo="termo")
    achadas = memoria.notas_relevantes(ana, "quem está inadimplente hoje?")
    assert achadas[0] == alvo and len(achadas) <= memoria.MAX_NOTAS_CONTEXTO
    assert vencida not in achadas and all(n.usuario_id == ana.pk for n in achadas)


def test_consultar_e_desativar_as_proprias_memorias(base):
    ana, admin = base["ana"], base["admin"]
    minha = memoria.lembrar(ana, "prefiro valores por mês", "usuario", tipo="preferencia")
    empresa = memoria.lembrar(admin, "locação é o contrato de aluguel de máquinas", "setor", tipo="termo")
    assert empresa.situacao == "aprovada"
    c = conversas.nova_individual(ana)
    msg, _ = conversas.enviar(ana, c, "o que você lembra?", FakeProvider([_d(acao="listar_memorias")]))
    assert f"nota-{minha.pk}" in msg.texto and f"nota-{empresa.pk}" in msg.texto

    msg, _ = conversas.enviar(ana, c, "esqueça", FakeProvider([_d(acao="esquecer_memoria", alvo=f"nota-{empresa.pk}",
                                                                  confirmado=True)]))
    empresa.refresh_from_db()
    assert empresa.ativo and "quem aprova definições" in msg.texto  # sem permissão no nível do setor

    msg, _ = conversas.enviar(ana, c, "esqueça", FakeProvider([_d(acao="esquecer_memoria", alvo=f"nota-{minha.pk}")]))
    assert "Confirma" in msg.texto
    conversas.enviar(ana, c, "sim", FakeProvider([_d(acao="esquecer_memoria", alvo=f"nota-{minha.pk}", confirmado=True)]))
    minha.refresh_from_db()
    assert not minha.ativo and minha not in memoria.minhas_memorias(ana)
