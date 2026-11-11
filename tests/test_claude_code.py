import json
import os
import sys
from io import StringIO

import pytest
from django.core.exceptions import ImproperlyConfigured

from iativa.agent import claude_code as cc
from iativa.agent.decisao import EntradaAgente
from iativa.agent.esquema import EntradaModelo
from iativa.agent.provedores import FalhaModelo, obter_provedor


def entrada(historico=None):
    return EntradaAgente(
        mensagem="consulta fictícia", hoje="2026-11-11", historico=historico or [],
        objetivos=[{"codigo": "teste", "nome": "Teste", "parametros": [{"codigo": "data"}]}],
        solicitacao_atual=None,
    )


def decisao():
    return {"resposta": "Olá", "acao": "conversar", "objetivo": "", "parametros": [],
            "alvo": "", "nota": "", "confirmado": False, "opcoes": [], "alcance": ""}


@pytest.fixture
def provider(settings, monkeypatch):
    settings.IATIVA_AGENTE = {"provedor": "claude-code", "envio_liberado": True}
    monkeypatch.setattr(cc, "resolver_cli", lambda _: sys.executable)
    return cc.ClaudeCodeProvider()


def runner(monkeypatch, result=None, auth=None, code=0, stderr=""):
    calls = []

    def run(cmd, prompt, **kwargs):
        calls.append((cmd, prompt, kwargs))
        if cmd[-1] == "--help":
            return 0, " ".join(cc.FLAGS), ""
        if "status" in cmd:
            return 0, json.dumps(auth if auth is not None else {"loggedIn": True, "authMethod": "claude.ai"}), ""
        return code, json.dumps(result if result is not None else {
            "type": "result", "subtype": "success", "structured_output": decisao(),
        }), stderr

    monkeypatch.setattr(cc, "executar_cli", run)
    return calls


def test_chamada_sem_shell_prompt_fora_argv_e_controles(provider, monkeypatch):
    calls = runner(monkeypatch)
    assert provider.decidir(entrada()) == decisao()
    cmd, prompt, _ = calls[-1]
    assert "consulta fictícia" in prompt and "consulta fictícia" not in cmd
    for flag in cc.FLAGS:
        assert flag in cmd
    assert cmd[cmd.index("--tools") + 1] == ""
    assert cmd[cmd.index("--mcp-config") + 1] == '{"mcpServers":{}}'
    assert "--resume" not in cmd and "--continue" not in cmd


def test_historico_so_da_entrada_sem_sessao_compartilhada(provider, monkeypatch):
    calls = runner(monkeypatch)
    provider.decidir(entrada([{"quem": "usuario", "texto": "CONVERSA_A"}]))
    provider.decidir(entrada([{"quem": "usuario", "texto": "CONVERSA_B"}]))
    prompts = [p for cmd, p, _ in calls if "-p" in cmd]
    assert "CONVERSA_A" in prompts[0] and "CONVERSA_B" not in prompts[0]
    assert "CONVERSA_B" in prompts[1] and "CONVERSA_A" not in prompts[1]


def test_interpretar_contrato_antigo(provider, monkeypatch):
    raw = {"intencao": "ajuda", "objetivo": "", "parametros": [],
           "ambiguidade": {"pergunta": "", "opcoes": []}, "observacao": ""}
    runner(monkeypatch, {"type": "result", "subtype": "success", "structured_output": raw})
    e = EntradaModelo("oi", "2026-11-11", entrada().objetivos, None)
    assert provider.interpretar(e) == raw


@pytest.mark.parametrize("auth", [
    {"loggedIn": False}, {"loggedIn": True},
    {"loggedIn": True, "authMethod": "api_key"},
    {"loggedIn": True, "authMethod": "api_key_helper"},
    {"loggedIn": True, "authMethod": "third_party"},
])
def test_autenticacao_falha_fechada_sem_enviar(provider, monkeypatch, auth):
    calls = runner(monkeypatch, auth=auth)
    with pytest.raises(FalhaModelo):
        provider.decidir(entrada())
    assert not any("-p" in c[0] for c in calls)


def test_cli_antiga_nao_relaxa_isolamento(provider, monkeypatch):
    monkeypatch.setattr(cc, "executar_cli", lambda *a, **k: (0, "old help", ""))
    with pytest.raises(FalhaModelo, match="isolamento"):
        provider.verificar()


@pytest.mark.parametrize("result", [[], {}, {"type": "result", "subtype": "success"},
    {"type": "result", "subtype": "success", "structured_output": {**decisao(), "acao": "executar_shell"}},
    {"type": "result", "subtype": "success", "structured_output": {**decisao(), "sql": "SELECT segredo"}},
])
def test_saida_invalida_e_acao_proibida(provider, monkeypatch, result):
    runner(monkeypatch, result)
    with pytest.raises(FalhaModelo):
        provider.decidir(entrada())


@pytest.mark.parametrize("error,match", [("rate_limit 429", "Limite"),
    ("authentication_failed 401", "Login"), ("billing_error", "cobrança"), ("secret-error", "processo")])
def test_falhas_sanitizadas_sem_fallback(provider, monkeypatch, error, match):
    calls = runner(monkeypatch, {"is_error": True, "result": error}, code=1)
    with pytest.raises(FalhaModelo, match=match) as exc:
        provider.decidir(entrada())
    assert exc.value.temporaria
    assert error not in str(exc.value)
    assert len(calls) == 3


def test_json_malformado(provider, monkeypatch):
    runner(monkeypatch)
    original = cc.executar_cli
    monkeypatch.setattr(cc, "executar_cli", lambda cmd, p, **kw:
                        (0, "invalid json", "") if "-p" in cmd else original(cmd, p, **kw))
    with pytest.raises(FalhaModelo, match="JSON inválido"):
        provider.decidir(entrada())


def test_ambiente_nao_envia_segredos(monkeypatch):
    for key in ("IATIVA_ERP_PASSWORD", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_USE_VERTEX", "ANTHROPIC_BASE_URL"):
        monkeypatch.setenv(key, "segredo")
        assert key not in cc.ambiente()


def test_factory_e_liberação(provider, settings):
    assert isinstance(obter_provedor(), cc.ClaudeCodeProvider)
    settings.IATIVA_AGENTE["envio_liberado"] = False
    with pytest.raises(ImproperlyConfigured):
        obter_provedor()


def test_nao_executa_shim_windows(monkeypatch):
    monkeypatch.setattr(cc.shutil, "which", lambda _: "C:/npm/claude.cmd")
    with pytest.raises(ImproperlyConfigured, match="nativo"):
        cc.resolver_cli("claude")


def test_subprocesso_real_sem_modelo(tmp_path):
    code, out, err = cc.executar_cli(
        [sys.executable, "-c", "import sys; print(sys.stdin.read()); print('err', file=sys.stderr)"],
        "prompt fictício", env=cc.ambiente(), timeout=5,
    )
    assert code == 0 and out.strip() == "prompt fictício" and err.strip() == "err"


def test_subprocesso_timeout():
    with pytest.raises(FalhaModelo, match="tempo limite"):
        cc.executar_cli([sys.executable, "-c", "import time; time.sleep(30)"], "", env=cc.ambiente(), timeout=0.1)


def test_subprocesso_limite_de_saida(monkeypatch):
    monkeypatch.setattr(cc, "MAX_SAIDA", 8192)
    with pytest.raises(FalhaModelo, match="limites"):
        cc.executar_cli([sys.executable, "-c", "print('x' * 100000)"], "", env=cc.ambiente(), timeout=5)


@pytest.mark.django_db
def test_interpretacao_compila_valida_sql_sem_executar(
    provider, monkeypatch, erp_sintetico, settings, novo_usuario, politica,
):
    from django.core.management import call_command
    from iativa.accounts.models import Department
    from iativa.agent import orquestrador
    from iativa.metadata.catalogo import abrir_catalogo
    from iativa.query import servico
    from iativa.query.compilador import compilar
    from iativa.query.plano import montar
    from iativa.query.validador import validar

    settings.IATIVA_METADADOS = {"fonte": "sintetico"}
    call_command("seed_exemplos", stdout=StringIO())
    setor = Department.objects.get(codigo="financeiro-cobranca")
    user = novo_usuario("claude-teste", setor)
    politica(user, setor, consultar=True, filiais=["001001"])
    raw = {"intencao": "nova_consulta", "objetivo": "titulos-em-atraso",
           "parametros": [{"codigo": c, "valor": v} for c, v in (
               ("data-corte", "01/01/2026"), ("dias-tolerancia-atraso", "0"),
               ("tratamento-baixa-parcial", "considerar-saldo"), ("filial-analise", "001001"))],
           "ambiguidade": {"pergunta": "", "opcoes": []}, "observacao": ""}
    runner(monkeypatch, {"type": "result", "subtype": "success", "structured_output": raw})

    def proibido(*a, **kw):
        pytest.fail("O teste de geração não deve executar consulta de dados.")

    monkeypatch.setattr(servico, "executar", proibido)
    result = orquestrador.processar_mensagem(user, "consulta fictícia", provedor=provider)
    assert result.tipo == "pronta"
    plano = montar(result.solicitacao.objetivo, servico._valores(user, result.solicitacao))
    with abrir_catalogo() as cat:
        sql = compilar(plano, cat, 10)
        tabelas = {t.fisica for t in cat.tabelas([plano.base] + [j.tabela for j in plano.juncoes]).values()}
        validar(sql.sql, tabelas, 11)
    assert "SELECT" in sql.sql and sql.params


def test_comando_diagnostico_sem_envio(provider, monkeypatch):
    from django.core.management import call_command

    calls = runner(monkeypatch)
    out = StringIO()
    call_command("testar_claude", stdout=out)
    assert "login Claude confirmado" in out.getvalue()
    assert not any("-p" in c[0] for c in calls)
