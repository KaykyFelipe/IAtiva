"""M114: parar o pedido que espera a manutenção cancela a correção (fila, agente rodando e o que já publicou)."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from django.core.management import call_command
from django.urls import reverse

from iativa.agent.models import Objetivo, SituacaoObjetivo as SO
from iativa.manutencao.models import Incidente
from .test_corrigindo_m50 import ANUNCIOS, acordar  # noqa: F401
from .test_objetivos import _enviar
from .test_tarefas import base  # noqa: F401

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import manutencao as mx  # noqa: E402

pytestmark = pytest.mark.django_db


def test_parar_cancela_a_correcao_e_sinaliza_o_executor(base, acordar, client, capsys):  # noqa: F811
    _, _, _, c = _enviar(base, "quem foi demitido nos últimos meses?", ANUNCIOS)
    o = Objetivo.objects.get()
    client.force_login(base["u"])
    client.post(reverse("chat:parar_objetivo", args=[o.pk]), HTTP_HX_REQUEST="true")
    inc = Incidente.objects.get(pk=o.incidente_id)
    assert inc.situacao == "bloqueado_admin" and inc.motivo.startswith("Cancelada pela pessoa")
    assert (acordar.parent / "manutencao-cancelar" / str(inc.pk)).exists()
    call_command("manutencao", "cancelado", inc.pk)
    assert capsys.readouterr().out.strip() == "sim"
    call_command("manutencao", "concluir", inc.pk, "publicado", "--commit", "abc")
    inc.refresh_from_db()
    assert inc.situacao == "bloqueado_admin"  # o executor não desfaz o cancelamento


def test_agente_rodando_e_interrompido_ao_cancelar(tmp_path, monkeypatch):
    monkeypatch.setattr(mx, "pasta_cancelar", lambda: tmp_path)
    (tmp_path / "7").write_text("x")
    cmd = [sys.executable, "-c", "import time; time.sleep(30)"]
    with pytest.raises(mx.Cancelada):
        mx.rodar_agente(cmd, 7, 60)


def test_agente_sem_cancelamento_termina_normal(tmp_path, monkeypatch):
    monkeypatch.setattr(mx, "pasta_cancelar", lambda: tmp_path)
    r = mx.rodar_agente([sys.executable, "-c", "print('NAO_REPRODUZIDO')"], 8, 60)
    assert "NAO_REPRODUZIDO" in r.stdout


def test_tempo_esgotado_interrompe(tmp_path, monkeypatch):
    monkeypatch.setattr(mx, "pasta_cancelar", lambda: tmp_path)
    with pytest.raises(subprocess.TimeoutExpired):
        mx.rodar_agente([sys.executable, "-c", "import time; time.sleep(30)"], 9, 1)


@pytest.mark.skipif(not shutil.which("git"), reason="requer git")
def test_correcao_publicada_e_desfeita_ao_cancelar(tmp_path, monkeypatch):
    def g(*a, cwd):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@x", *a], cwd=cwd, check=True,
                              capture_output=True, text=True).stdout
    remoto, repo = tmp_path / "r.git", tmp_path / "repo"
    g("init", "-q", "--bare", "-b", "main", str(remoto), cwd=tmp_path)
    g("clone", "-q", str(remoto), str(repo), cwd=tmp_path)
    (repo / "a.txt").write_text("v1\n"); g("add", "-A", cwd=repo); g("commit", "-qm", "v1", cwd=repo)
    g("push", "-q", "origin", "HEAD:main", cwd=repo); g("fetch", "-q", cwd=repo)
    (repo / "a.txt").write_text("corrigido\n"); g("commit", "-qam", "fix(manutenção): incidente #12 x", cwd=repo)
    canc = tmp_path / "cancelar"; canc.mkdir(); (canc / "12").write_text("x")
    publicadas = []

    def rodar(cmd, **k):
        if cmd[0].endswith("publicar_local.sh"):  # simula: avança a main até a branch com o revert
            g("merge", "-q", "--ff-only", cmd[1], cwd=repo)
            publicadas.append(cmd[1])
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.run(cmd, cwd=k.get("cwd"), capture_output=True, text=True,
                              env={**__import__("os").environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
                                   "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"})

    monkeypatch.setattr(mx, "REPO", repo)
    monkeypatch.setattr(mx, "TRABALHO", tmp_path / "trab")
    (tmp_path / "trab").mkdir()
    monkeypatch.setattr(mx, "pasta_cancelar", lambda: canc)
    monkeypatch.setattr(mx, "rodar", rodar)
    monkeypatch.setattr(mx, "log", lambda t: None)
    assert mx.reverter_cancelados() == [12]
    assert publicadas == ["manutencao/inc-12"] and (repo / "a.txt").read_text() == "v1\n"
    assert not (canc / "12").exists()


def test_saida_do_agente_maior_que_o_buffer_nao_trava(tmp_path, monkeypatch):
    monkeypatch.setattr(mx, "pasta_cancelar", lambda: tmp_path)
    r = mx.rodar_agente([sys.executable, "-c", "print('x' * 300000)"], 8, 10)
    assert r.returncode == 0 and len(r.stdout) > 300000


@pytest.mark.parametrize("motivo,versao_nova,reabre", [
    ("Cancelada pela pessoa (botão parar).", False, True),
    ("Voltou a ocorrer depois da correção; precisa de análise humana.", True, True),
    ("Voltou a ocorrer depois da correção; precisa de análise humana.", False, False)])
def test_m124_mesmo_pedido_de_novo_reabre_o_registro(monkeypatch, motivo, versao_nova, reabre):
    from iativa.manutencao import incidentes
    cond = {"trilha": ["preparar_consulta:acao_repetida:-"], "pergunta": "quantos?"}
    monkeypatch.setattr(incidentes, "versao", lambda: "v1")
    inc = incidentes._registrar("agente", "op", categoria="capacidade_ausente", condicoes=cond)
    Incidente.objects.filter(pk=inc.pk).update(situacao="bloqueado_admin", motivo=motivo)
    monkeypatch.setattr(incidentes, "versao", lambda: "v2" if versao_nova else "v1")
    inc = incidentes._registrar("agente", "op", categoria="capacidade_ausente", condicoes=cond)
    assert (inc.situacao == "aberto") is reabre
