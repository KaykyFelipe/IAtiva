"""M10: agente conversacional — ações, troca de assunto, memória, remoção de aprendizado, Gemini."""
import json
from io import StringIO
from types import SimpleNamespace

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.http import QueryDict

from iativa.accounts.models import Department
from iativa.agent import agente, aprendizado
from iativa.agent.decisao import DecisaoInvalida, EntradaAgente, validar
from iativa.agent.models import ConsultaModelo, NotaAgente, SituacaoModelo
from iativa.agent.provedores import FakeProvider, FalhaModelo, GeminiProvider
from iativa.chat import conversas
from iativa.chat.models import EstadoSolicitacao as E, RequestContext

pytestmark = pytest.mark.django_db
ATRASO = "inadimplentes corte 2026-01-01, 0 dias, considerar saldo, filial 01"


@pytest.fixture(autouse=True)
def _base(erp_sintetico, settings):
    settings.IATIVA_METADADOS = {"fonte": "sintetico"}
    call_command("seed_exemplos", stdout=StringIO())


@pytest.fixture
def ana(novo_usuario, politica):
    setor = Department.objects.get(codigo="financeiro-cobranca")
    u = novo_usuario("ana", setor)
    politica(u, setor, consultar=True, filiais=["01"])
    return u


@pytest.fixture
def admin(novo_usuario, politica, settings):
    settings.IATIVA_APRENDIZADO_AUTOAPROVACAO = True
    cob = Department.objects.get(codigo="financeiro-cobranca")
    u = novo_usuario("diego", cob)
    politica(u, cob, consultar=True, administrar=True, aprovar_conhecimento=True, filiais=["01"])
    politica(u, None, aprovar_conhecimento=True, administrar=True)
    return u


def _decisao(**kw):
    base = {"resposta": "", "acao": "conversar", "objetivo": "", "parametros": [], "alvo": "", "nota": "",
            "confirmado": False, "opcoes": []}
    return {**base, **kw}


def _falar(user, c, texto, provedor=None):
    return conversas.enviar(user, c, texto, provedor=provedor)


def test_troca_de_assunto_cancela_a_anterior_sem_clique(ana):
    c = conversas.nova_individual(ana)
    _falar(ana, c, "quais clientes estão inadimplentes?")
    primeira = RequestContext.objects.get()
    assert primeira.estado == E.AGUARDANDO_PARAMETROS
    _falar(ana, c, "na verdade quero os títulos a vencer de 01/01/2026 a 31/03/2026 filial 01 considerar saldo")
    primeira.refresh_from_db()
    assert primeira.estado == E.CANCELADA
    segunda = RequestContext.objects.exclude(pk=primeira.pk).get()
    assert segunda.objetivo == "titulos-a-vencer" and segunda.estado == E.CONCLUIDA  # executou sozinha


def test_resposta_do_modelo_vira_texto_da_conversa(ana):
    c = conversas.nova_individual(ana)
    p = FakeProvider([_decisao(resposta="Oi, Ana! Posso consultar inadimplência, títulos a vencer ou um cliente.")])
    msg, _ = _falar(ana, c, "bom dia", p)
    assert msg.texto.startswith("Oi, Ana!") and not RequestContext.objects.exists()


def test_modelo_pergunta_o_que_falta_com_as_proprias_palavras(ana):
    c = conversas.nova_individual(ana)
    p = FakeProvider([_decisao(resposta="Claro! Até que data e com quantos dias de tolerância?",
                               acao="preparar_consulta", objetivo="titulos-em-atraso")])
    msg, res = _falar(ana, c, "quem tá devendo?", p)
    assert "Até que data" in msg.texto and res is None


def test_execucao_automatica_descarta_pergunta_de_confirmacao(ana):
    c = conversas.nova_individual(ana)
    p = FakeProvider([_decisao(resposta="Tudo certo. Posso executar a consulta?", acao="preparar_consulta",
                               objetivo="titulos-em-atraso", opcoes=["Sim, executar", "Cancelar"],
                               parametros=[{"codigo": "data-corte", "valor": "01/01/2026"},
                                           {"codigo": "dias-tolerancia-atraso", "valor": "0"},
                                           {"codigo": "tratamento-baixa-parcial", "valor": "considerar-saldo"},
                                           {"codigo": "filial-analise", "valor": "01"}])])
    msg, res = _falar(ana, c, "x", p)
    assert res is not None and "Posso executar" not in msg.texto and msg.ficha["opcoes"] == []


def test_contexto_vai_ao_modelo_sem_resultados(ana):
    c = conversas.nova_individual(ana)
    msg, res = _falar(ana, c, ATRASO)
    assert res is not None and res.ok
    capturado = {}

    class Espiao(FakeProvider):
        def decidir(self, entrada):
            capturado["e"] = entrada
            return _decisao(resposta="ok")

    _falar(ana, c, "obrigado", Espiao())
    e = capturado["e"]
    assert any("Consulta executada" in h["texto"] for h in e.historico)
    assert "CLIENTE SINTETICO" not in json.dumps(e.__dict__, default=str, ensure_ascii=False)


def test_lembrar_guarda_e_volta_no_contexto(ana, admin):
    c = conversas.nova_individual(admin)
    _falar(admin, c, "lembre que inadimplente para nós é atraso acima de 5 dias")
    nota = NotaAgente.objects.get()
    assert nota.escopo == "empresa" and "5 dias" in nota.texto
    c2 = conversas.nova_individual(ana)
    capturado = {}

    class Espiao(FakeProvider):
        def decidir(self, entrada):
            capturado["notas"] = entrada.notas
            return _decisao(resposta="ok")

    _falar(ana, c2, "oi", Espiao())
    assert any("5 dias" in n for n in capturado["notas"])


def test_lembrar_recusa_cpf(admin):
    c = conversas.nova_individual(admin)
    p = FakeProvider([_decisao(acao="lembrar", nota="cliente 123.456.789-09 é especial")])
    msg, _ = _falar(admin, c, "guarde isso", p)
    assert "CPF" in msg.texto and not NotaAgente.objects.exists()


def _modelo(admin):
    q = QueryDict(mutable=True)
    q.update({"nome": "Títulos emitidos no período", "campo_data": "E1_EMISSAO"})
    q.setlist("colunas", ["E1_NUM", "E1_EMISSAO"])
    q.setlist("setores", [str(Department.objects.get(codigo="financeiro-cobranca").pk)])
    cm = aprendizado.salvar_proposta(admin, "SE1", q)
    return aprendizado.aprovar(admin, cm)


def test_remover_aprendizado_pede_confirmacao(admin):
    cm = _modelo(admin)
    c = conversas.nova_individual(admin)
    msg, _ = _falar(admin, c, "remova o aprendizado títulos emitidos no período")
    assert "Confirma" in msg.texto and msg.ficha["opcoes"][0].startswith("Sim, remover")
    cm.refresh_from_db()
    assert cm.situacao == SituacaoModelo.APROVADA
    msg, _ = _falar(admin, c, msg.ficha["opcoes"][0])
    cm.refresh_from_db()
    assert cm.situacao == SituacaoModelo.REMOVIDA and "removi" in msg.texto
    assert cm.codigo not in {o.codigo for o in agente.objetivos_para(admin)}


def test_remover_exige_administrador(ana, admin):
    _modelo(admin)
    c = conversas.nova_individual(ana)
    msg, _ = _falar(ana, c, "sim, remova o aprendizado títulos emitidos no período")
    assert "só para administradores" in msg.texto
    assert ConsultaModelo.objects.get().situacao == SituacaoModelo.APROVADA


def test_listar_aprendizados(admin):
    _modelo(admin)
    c = conversas.nova_individual(admin)
    msg, _ = _falar(admin, c, "quais aprendizados você tem?")
    assert "Títulos emitidos no período" in msg.texto


def test_falha_do_modelo_resposta_amigavel(ana):
    c = conversas.nova_individual(ana)
    msg, _ = _falar(ana, c, "x", FakeProvider([FalhaModelo("rede"), FalhaModelo("rede")]))
    assert "Não consegui processar" in msg.texto


def test_decisao_valida_estritamente():
    with pytest.raises(DecisaoInvalida):
        validar(_decisao(acao="apagar_banco"), [], [])
    with pytest.raises(DecisaoInvalida):
        validar({**_decisao(), "sql": "SELECT 1"}, [], [])
    with pytest.raises(DecisaoInvalida):
        validar(_decisao(objetivo="inventado"), ["titulos-em-atraso"], [])


# --- Gemini (sem rede) ---------------------------------------------------------------------

class _ClienteGemini:
    def __init__(self, texto):
        self.texto, self.chamada = texto, None
        self.models = SimpleNamespace(generate_content=self._gerar)

    def _gerar(self, **kw):
        self.chamada = kw
        return SimpleNamespace(text=self.texto)


def _entrada():
    return EntradaAgente(mensagem="oi", hoje="2026-11-11", historico=[], objetivos=[], solicitacao_atual=None)


def test_gemini_exige_liberacao(settings):
    settings.IATIVA_AGENTE = {**settings.IATIVA_AGENTE, "envio_liberado": False}
    with pytest.raises(ImproperlyConfigured):
        GeminiProvider()


def test_gemini_usa_json_schema(settings):
    settings.IATIVA_AGENTE = {**settings.IATIVA_AGENTE, "envio_liberado": True}
    cliente = _ClienteGemini(json.dumps(_decisao(resposta="Olá")))
    saida = GeminiProvider(cliente=cliente).decidir(_entrada())
    assert saida["resposta"] == "Olá"
    cfg = cliente.chamada["config"]
    assert cliente.chamada["model"] == "gemini-3.8-flash"
    assert cfg.response_mime_type == "application/json" and cfg.response_json_schema["required"]
    assert "IAtiva" in cfg.system_instruction


@pytest.mark.parametrize("texto", [None, "", "{quebrado"])
def test_gemini_resposta_ruim_vira_falha(settings, texto):
    settings.IATIVA_AGENTE = {**settings.IATIVA_AGENTE, "envio_liberado": True}
    with pytest.raises(FalhaModelo):
        GeminiProvider(cliente=_ClienteGemini(texto)).decidir(_entrada())


class _ClienteInstavel:
    """Simula o Gemini: devolve os códigos de erro da fila e depois responde."""

    def __init__(self, erros, texto):
        self.erros, self.texto, self.modelos = list(erros), texto, []
        self.models = SimpleNamespace(generate_content=self._gerar)

    def _gerar(self, **kw):
        from google.genai import errors

        self.modelos.append(kw["model"])
        if self.erros:
            codigo = self.erros.pop(0)
            raise errors.APIError(codigo, {"error": {"code": codigo, "message": "x", "status": "X"}})
        return SimpleNamespace(text=self.texto)


@pytest.fixture
def _gemini(settings, monkeypatch):
    settings.IATIVA_AGENTE = {**settings.IATIVA_AGENTE, "envio_liberado": True, "gemini_modelo": "principal",
                              "gemini_reserva": ["reserva"]}
    monkeypatch.setattr("iativa.agent.provedores.time.sleep", lambda s: None)


def test_gemini_sobrecarregado_tenta_de_novo(_gemini):
    cliente = _ClienteInstavel([503, 429], json.dumps(_decisao(resposta="Olá")))
    assert GeminiProvider(cliente=cliente).decidir(_entrada())["resposta"] == "Olá"
    assert cliente.modelos == ["principal"] * 3


def test_gemini_usa_modelo_reserva(_gemini):
    cliente = _ClienteInstavel([503, 503, 429], json.dumps(_decisao(resposta="Olá")))
    GeminiProvider(cliente=cliente).decidir(_entrada())
    assert cliente.modelos == ["principal"] * 3 + ["reserva"]


def test_gemini_indisponivel_vira_falha_temporaria(_gemini):
    cliente = _ClienteInstavel([503] * 4, "{}")
    with pytest.raises(FalhaModelo) as exc:
        GeminiProvider(cliente=cliente).decidir(_entrada())
    assert exc.value.temporaria


def test_erro_definitivo_nao_espera(_gemini):
    cliente = _ClienteInstavel([400], "{}")
    with pytest.raises(FalhaModelo) as exc:
        GeminiProvider(cliente=cliente).decidir(_entrada())
    assert not exc.value.temporaria and cliente.modelos == ["principal"]


def test_falha_temporaria_explica_ao_usuario(ana):
    c = conversas.nova_individual(ana)
    msg, _ = _falar(ana, c, "x", FakeProvider([FalhaModelo("503", temporaria=True)]))
    assert "sobrecarregada ou no limite" in msg.texto


def test_parar_descarta_o_envio(ana):
    import uuid

    from iativa.agent.agente import Interrompido
    from iativa.chat.models import Mensagem

    c = conversas.nova_individual(ana)
    pedido = uuid.uuid4()
    conversas.interromper(ana, pedido)
    with pytest.raises(Interrompido):
        conversas.enviar(ana, c, ATRASO, pedido=pedido)
    assert not Mensagem.objects.filter(conversa=c).exists() and not RequestContext.objects.exists()


def test_parar_de_outro_usuario_nao_afeta(ana, admin):
    import uuid

    pedido = uuid.uuid4()
    conversas.interromper(admin, pedido)
    c = conversas.nova_individual(ana)
    msg, res = conversas.enviar(ana, c, ATRASO, pedido=pedido)
    assert res is not None and res.ok


def test_views_parar_e_mensagem_interrompida(client, ana):
    import uuid

    from django.urls import reverse

    from iativa.chat.models import Mensagem

    c = conversas.nova_individual(ana)
    client.force_login(ana)
    pedido = str(uuid.uuid4())
    assert client.post(reverse("chat:parar"), {"pedido": pedido}).status_code == 204
    assert client.post(reverse("chat:parar"), {"pedido": "x"}).status_code == 400
    r = client.post(reverse("chat:mensagem", args=[c.pk]), {"mensagem": ATRASO, "pedido": pedido})
    assert r.status_code == 204 and not Mensagem.objects.filter(conversa=c).exists()
    r = client.post(reverse("chat:mensagem", args=[c.pk]), {"mensagem": ATRASO, "pedido": pedido})
    assert r.status_code == 200 and Mensagem.objects.filter(conversa=c).exists()  # marca usada e apagada
