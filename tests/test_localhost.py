"""Acesso exclusivo por 127.0.0.1 (seção 5 do pedido)."""
import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError, SystemCheckError

from iativa.core.management.commands import runserver as runserver_mod


from config.settings import base as base_settings


@pytest.fixture(autouse=True)
def _hosts_reais(settings, monkeypatch, tmp_path):
    # O ambiente de testes do Django acrescenta "testserver"; aqui validamos a configuração real.
    settings.ALLOWED_HOSTS = list(base_settings.ALLOWED_HOSTS)
    # Testes independentes de onde rodam (máquina ou container): sem exceção de container por padrão.
    monkeypatch.delenv(runserver_mod.CONTAINER_BIND_ENV, raising=False)
    monkeypatch.setattr(runserver_mod, "CONTAINER_MARKER", tmp_path / "nao-e-container")


def test_allowed_hosts_somente_localhost():
    assert set(base_settings.ALLOWED_HOSTS) == {"127.0.0.1", "localhost"}


def test_debug_desligado():
    assert base_settings.DEBUG is False
    assert settings.DEBUG is False


@pytest.mark.parametrize("hosts", [["*"], ["0.0.0.0"], ["127.0.0.1", "192.168.0.10"], ["iativa.empresa.com.br"], []])
def test_check_falha_com_hosts_externos(hosts, settings):
    settings.ALLOWED_HOSTS = hosts
    with pytest.raises(SystemCheckError, match="iativa.E001"):
        call_command("check")


def test_check_falha_com_debug_ligado(settings):
    settings.DEBUG = True
    with pytest.raises(SystemCheckError, match="iativa.E002"):
        call_command("check")


def test_check_passa_na_configuracao_padrao():
    call_command("check")


@pytest.mark.parametrize("addrport", ["0.0.0.0:8000", "0:8000", "192.168.0.10:8000", "10.0.0.1:8000", "[::]:8000"])
def test_runserver_recusa_enderecos_externos(addrport, monkeypatch):
    chamadas = []
    monkeypatch.setattr(runserver_mod.Command, "inner_run", lambda self, *a, **k: chamadas.append(self.addr))
    with pytest.raises(CommandError):
        call_command("runserver", addrport, use_reloader=False, skip_checks=True)
    assert chamadas == []


def test_runserver_recusa_ipv6(monkeypatch):
    monkeypatch.setattr(runserver_mod.Command, "inner_run", lambda self, *a, **k: None)
    with pytest.raises(CommandError, match="IPv6"):
        call_command("runserver", use_ipv6=True, use_reloader=False, skip_checks=True)


@pytest.mark.parametrize("args", [(), ("8000",), ("127.0.0.1:8001",), ("localhost:8002",)])
def test_runserver_aceita_localhost(args, monkeypatch):
    chamadas = []
    monkeypatch.setattr(
        runserver_mod.Command, "inner_run", lambda self, *a, **k: chamadas.append((self.addr, k["insecure_serving"]))
    )
    call_command("runserver", *args, use_reloader=False, skip_checks=True)
    assert len(chamadas) == 1
    addr, insecure = chamadas[0]
    assert addr in {"127.0.0.1", "localhost"}
    assert insecure is True


def _simular_container(monkeypatch, tmp_path, *, marcador: bool, variavel: bool):
    marker = tmp_path / ".dockerenv"
    if marcador:
        marker.touch()
    monkeypatch.setattr(runserver_mod, "CONTAINER_MARKER", marker)
    if variavel:
        monkeypatch.setenv(runserver_mod.CONTAINER_BIND_ENV, "1")


@pytest.mark.parametrize("marcador, variavel", [(False, False), (True, False), (False, True)])
def test_container_exige_marcador_e_variavel(marcador, variavel, monkeypatch, tmp_path):
    _simular_container(monkeypatch, tmp_path, marcador=marcador, variavel=variavel)
    monkeypatch.setattr(runserver_mod.Command, "inner_run", lambda self, *a, **k: None)
    with pytest.raises(CommandError):
        call_command("runserver", "0.0.0.0:8000", use_reloader=False, skip_checks=True)


def test_container_aceita_0000_com_as_duas_condicoes(monkeypatch, tmp_path):
    _simular_container(monkeypatch, tmp_path, marcador=True, variavel=True)
    chamadas = []
    monkeypatch.setattr(runserver_mod.Command, "inner_run", lambda self, *a, **k: chamadas.append(self.addr))
    call_command("runserver", "0.0.0.0:8000", use_reloader=False, skip_checks=True)
    assert chamadas == ["0.0.0.0"]


@pytest.mark.parametrize("addrport", ["192.168.0.10:8000", "10.0.0.1:8000"])
def test_container_continua_recusando_ips_da_rede(addrport, monkeypatch, tmp_path):
    _simular_container(monkeypatch, tmp_path, marcador=True, variavel=True)
    monkeypatch.setattr(runserver_mod.Command, "inner_run", lambda self, *a, **k: None)
    with pytest.raises(CommandError):
        call_command("runserver", addrport, use_reloader=False, skip_checks=True)


def _portas_publicadas(texto: str) -> list[str]:
    portas, em_ports, indent = [], False, 0
    for linha in texto.splitlines():
        limpa = linha.strip()
        if not limpa or limpa.startswith("#"):
            continue
        atual = len(linha) - len(linha.lstrip())
        if limpa == "ports:":
            em_ports, indent = True, atual
            continue
        if em_ports and atual > indent and limpa.startswith("- "):
            portas.append(limpa[2:].strip().strip("\"'"))
        elif em_ports and atual <= indent:
            em_ports = False
    return portas


def test_compose_publica_somente_em_localhost():
    from pathlib import Path

    compose = (Path(settings.BASE_DIR) / "compose.yaml").read_text(encoding="utf-8")
    portas = _portas_publicadas(compose)
    assert len(portas) >= 2, portas
    for porta in portas:
        assert porta.startswith("127.0.0.1:"), porta
    assert "network_mode: host" not in compose
