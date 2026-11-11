import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.urls import reverse

from iativa.agent import claude_login as cl
from iativa.agent.claude_code import ClaudeCodeProvider, resolver_cli
from iativa.agent.provedores import FalhaModelo, obter_provedor


pytestmark = pytest.mark.django_db
URL = "https://claude.ai/oauth/authorize?state=ficticio"
CODE = "12345678#state"


def esperar(check):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.01)
    pytest.fail("O processo de login não chegou ao estado esperado.")


@pytest.fixture
def admin(django_user_model):
    return django_user_model.objects.create_superuser("dono-claude", password="senha-ficticia")


@pytest.fixture
def fake(tmp_path, settings, monkeypatch):
    settings.IATIVA_AGENTE = {
        "provedor": "teste", "envio_liberado": False,
        "claude_contas_dir": str(tmp_path / "contas"),
    }
    script = tmp_path / "fake-cli.py"
    script.write_text(
        "import os, sys\nfrom pathlib import Path\n"
        f"print({URL!r}, flush=True)\n"
        "code = sys.stdin.readline().strip()\n"
        f"if code == {CODE!r}:\n"
        "    (Path(os.environ['CLAUDE_CONFIG_DIR']) / 'fake-auth').touch()\n"
        "    print('OK', flush=True)\n"
        "else:\n    sys.exit(1)\n", encoding="utf-8",
    )
    calls = []

    class Provider:
        def __init__(self, user):
            self.cfg = cl.config_usuario(user)
            from iativa.agent.claude_code import ambiente

            self.env = ambiente(self.cfg["claude_config_dir"])
            self.cli = sys.executable

        def checar_cli(self):
            pass

        def _executar(self, args, **kwargs):
            calls.append((args, self.cfg["claude_config_dir"]))
            flag = Path(self.cfg["claude_config_dir"]) / "fake-auth"
            if "logout" in args:
                flag.unlink(missing_ok=True)
                return 0, "", ""
            ok = flag.exists()
            return (0 if ok else 1), json.dumps({"loggedIn": ok, "authMethod": "claude.ai" if ok else "none"}), ""

    original = subprocess.Popen

    def popen(args, **kwargs):
        assert kwargs["shell"] is False
        assert args[-2:] == ["auth", "login"]
        assert "IATIVA_ERP_PASSWORD" not in kwargs["env"]
        assert "ANTHROPIC_API_KEY" not in kwargs["env"]
        return original([sys.executable, str(script)], **kwargs)

    manager = cl.LoginPessoal()
    monkeypatch.setattr(cl, "provedor_login", Provider)
    monkeypatch.setattr(cl.subprocess, "Popen", popen)
    monkeypatch.setattr(cl, "login", manager)
    # claude_views imports the singleton at module load.
    from iativa.agent import claude_views
    monkeypatch.setattr(claude_views, "login", manager)
    monkeypatch.setattr(claude_views, "provedor_login", Provider)
    yield SimpleNamespace(manager=manager, calls=calls, root=tmp_path / "contas", Provider=Provider)
    manager.fechar()


def pronto(fake, user, session="sessao-a"):
    state = fake.manager.iniciar(user, session)
    fluxo = fake.manager.obter(user, session, state["id"])
    esperar(lambda: fluxo.estado()["fase"] == "aguardando")
    return fluxo


@pytest.mark.parametrize("url", [
    "http://claude.ai/oauth/authorize", "https://claude.ai.evil.test/oauth/authorize",
    "https://evil.test/oauth/authorize", "https://user:pass@claude.ai/oauth/authorize",
    "https://claude.ai:444/oauth/authorize", "https://claude.ai/docs", "javascript:alert(1)",
])
def test_rejeita_links_fora_login_oficial(url):
    assert cl.link_oficial(url + "\n") is None


def test_espera_link_completo_e_remove_ansi():
    assert cl.link_oficial(URL) is None
    assert cl.link_oficial("\x1b[32m" + URL + "\x1b[0m\n") == URL


def test_login_com_processo_real_simulado(admin, fake):
    fluxo = pronto(fake, admin)
    assert fluxo.estado()["url"] == URL
    fake.manager.concluir(admin, "sessao-a", fluxo.id, CODE)
    esperar(lambda: fluxo.estado()["fase"] == "conectado")
    assert cl.ativa(admin)
    assert fluxo.estado()["url"] == ""
    for p in fake.root.rglob("*"):
        if p.is_file():
            assert CODE.encode() not in p.read_bytes()


def test_nao_reutiliza_codigo_e_nao_ecoar(admin, fake, client):
    client.force_login(admin)
    response = client.post(reverse("claude-conta"), {"acao": "conectar"})
    assert response.status_code == 200
    fluxo = fake.manager.fluxos[admin.pk]
    esperar(lambda: fluxo.estado()["fase"] == "aguardando")
    response = client.get(reverse("claude-estado", args=[fluxo.id]))
    html = response.content.decode()
    assert "Abrir login do Claude" in html
    assert 'type="password"' in html and 'hx-trigger="every 2s"' not in html
    response = client.post(reverse("claude-conta"), {"acao": "concluir", "fluxo": fluxo.id, "codigo": CODE})
    assert CODE not in response.content.decode()
    assert "codigo" not in client.session
    esperar(lambda: fluxo.estado()["fase"] == "conectado")
    with pytest.raises(FalhaModelo):
        fake.manager.concluir(admin, cl.chave_sessao(response.wsgi_request), fluxo.id, CODE)


def test_outro_usuario_ou_sessao_nao_acessa_fluxo(admin, fake, django_user_model):
    outro = django_user_model.objects.create_superuser("outro-admin", password="senha-ficticia")
    fluxo = pronto(fake, admin)
    with pytest.raises(FalhaModelo):
        fake.manager.obter(outro, "sessao-a", fluxo.id)
    with pytest.raises(FalhaModelo):
        fake.manager.concluir(admin, "sessao-b", fluxo.id, CODE)
    with pytest.raises(FalhaModelo):
        fake.manager.cancelar(admin, "sessao-b", fluxo.id)
    with pytest.raises(FalhaModelo):
        fake.manager.iniciar(admin, "sessao-b")
    assert not cl.ativa(outro)


@pytest.mark.parametrize("code", ["", "curto", "a" * 1025, "abcdefg\n123", "a b c d e f", "<script>test</script>"])
def test_codigo_invalido_nao_entregue(admin, fake, code):
    fluxo = pronto(fake, admin)
    with pytest.raises(FalhaModelo):
        fake.manager.concluir(admin, "sessao-a", fluxo.id, code)
    assert fluxo.enviou is False and fluxo.fase == "aguardando"


def test_cancelar_e_expirar_encerram_processo(admin, fake, monkeypatch):
    fluxo = pronto(fake, admin)
    fake.manager.cancelar(admin, "sessao-a", fluxo.id)
    assert fluxo.fase == "cancelado" and fluxo.proc.poll() is not None
    assert not cl.ativa(admin)
    monkeypatch.setattr(cl, "TEMPO_LOGIN", 0.1)
    state = fake.manager.iniciar(admin, "sessao-a")
    novo = fake.manager.obter(admin, "sessao-a", state["id"])
    esperar(lambda: novo.fase == "expirou")
    assert not cl.ativa(admin)


def test_desconecta_so_diretorio_pessoal(admin, fake):
    fluxo = pronto(fake, admin)
    fake.manager.concluir(admin, "sessao-a", fluxo.id, CODE)
    esperar(lambda: cl.ativa(admin))
    fake.manager.desconectar(admin, "sessao-a")
    assert not cl.ativa(admin)
    assert any("logout" in args for args, _ in fake.calls)
    assert all(str(fake.root / f"usuario-{admin.pk}") == directory for _, directory in fake.calls)


def test_controles_exigem_login_admin_e_csrf(admin, fake, client, usuario):
    assert client.get(reverse("claude-conta")).status_code == 302
    client.force_login(usuario)
    assert client.get(reverse("claude-conta")).status_code == 403
    assert client.post(reverse("claude-conta"), {"acao": "conectar"}).status_code == 403
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(admin)
    assert strict.post(reverse("claude-conta"), {"acao": "conectar"}).status_code == 403
    assert fake.manager.fluxos == {}


def test_restricao_username_e_no_store(admin, fake, client, settings):
    client.force_login(admin)
    response = client.get(reverse("claude-conta"))
    assert response.status_code == 200
    assert "no-store" in response["Cache-Control"]
    settings.IATIVA_AGENTE["claude_usuario"] = "outro"
    assert client.get(reverse("claude-conta")).status_code == 403


def test_somente_dono_usa_provedor_pessoal_e_preserva_privacidade(admin, fake, monkeypatch, settings, usuario):
    cfg = cl.config_usuario(admin)
    cl._marcar(cfg, True)
    monkeypatch.setattr("iativa.agent.claude_code.resolver_cli", lambda _: sys.executable)
    from django.core.exceptions import ImproperlyConfigured
    with pytest.raises(ImproperlyConfigured):
        obter_provedor(usuario=admin)
    settings.IATIVA_AGENTE["envio_liberado"] = True
    provider = obter_provedor(usuario=admin)
    assert isinstance(provider, ClaudeCodeProvider)
    assert provider.env["CLAUDE_CONFIG_DIR"] == cfg["claude_config_dir"]
    assert obter_provedor(usuario=usuario).nome == "teste"


def test_estado_inexistente_nao_expoe_links(admin, fake, client):
    client.force_login(admin)
    response = client.get(reverse("claude-estado", args=["00000000-0000-0000-0000-000000000000"]))
    assert "expirou" in response.content.decode()
    assert URL not in response.content.decode()


def test_detecta_cli_fora_path(tmp_path, monkeypatch):
    monkeypatch.setattr("iativa.agent.claude_code.shutil.which", lambda _: None)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    nome = "claude.exe" if sys.platform == "win32" else "claude"
    binary = tmp_path / ".local/bin" / nome
    binary.parent.mkdir(parents=True)
    binary.touch()
    assert resolver_cli("claude") == str(binary)


@pytest.mark.parametrize("resposta", [
    (0, '{"loggedIn":true,"authMethod":"api_key"}', ""),
    (0, "json invalido", ""),
    (0, "[]", ""),
])
def test_login_desconhecido_ou_api_nao_ativa(resposta):
    provider = SimpleNamespace(_executar=lambda *a, **kw: resposta)
    with pytest.raises(FalhaModelo):
        cl.conectado(provider)
