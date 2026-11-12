"""Rotina de atualização (scripts/atualizar.sh) com Docker simulado e repositório remoto local.

Não usa rede nem Docker real: o "GitHub" é um repositório bare em pasta temporária e o comando
docker é substituído por um script que registra as chamadas e falha quando pedido.
Pulado onde bash/git/flock não existem (ex.: dentro do container da aplicação).
"""
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from django.conf import settings

SCRIPT = Path(settings.BASE_DIR) / "scripts" / "atualizar.sh"

pytestmark = pytest.mark.skipif(
    not all(shutil.which(cmd) for cmd in ("bash", "git", "flock")),
    reason="requer bash, git e flock",
)

FAKE_IMPLANTAR = """#!/usr/bin/env bash
echo "implantar $*" >> "$FAKE_DOCKER_LOG"
exit "${FAKE_IMPLANTAR_CODIGO:-0}"
"""

FAKE_DOCKER = """#!/usr/bin/env bash
echo "$*" >> "$FAKE_DOCKER_LOG"
if [[ -n "${FAKE_DOCKER_FAIL:-}" && "$*" == *"$FAKE_DOCKER_FAIL"* ]]; then
    exit 1
fi
exit 0
"""


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"},
    ).stdout.strip()


@pytest.fixture
def ambiente(tmp_path):
    remoto = tmp_path / "remoto.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remoto))

    autor = tmp_path / "autor"
    _git(tmp_path, "clone", "-q", str(remoto), str(autor))
    (autor / "scripts").mkdir()
    shutil.copy(SCRIPT, autor / "scripts" / "atualizar.sh")
    (autor / "scripts" / "implantar.sh").write_text(FAKE_IMPLANTAR)
    (autor / "scripts" / "implantar.sh").chmod(0o755)
    (autor / ".gitignore").write_text("var/\n")
    (autor / "app.txt").write_text("v1\n")
    _git(autor, "add", "-A")
    _git(autor, "commit", "-q", "-m", "v1")
    _git(autor, "push", "-q", "origin", "HEAD:main")

    servidor = tmp_path / "servidor"
    _git(tmp_path, "clone", "-q", "-b", "main", str(remoto), str(servidor))

    docker = tmp_path / "docker-falso"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
    chamadas = tmp_path / "chamadas.log"
    chamadas.touch()

    def publicar(conteudo):
        (autor / "app.txt").write_text(conteudo)
        _git(autor, "commit", "-q", "-am", conteudo.strip())
        _git(autor, "push", "-q", "origin", "HEAD:main")
        return _git(autor, "rev-parse", "HEAD")

    def rodar(*args, falhar="", implantar="0"):
        env = {**os.environ, "DOCKER": str(docker), "FAKE_DOCKER_LOG": str(chamadas),
               "FAKE_DOCKER_FAIL": falhar, "FAKE_IMPLANTAR_CODIGO": implantar}
        return subprocess.run(
            ["bash", str(servidor / "scripts" / "atualizar.sh"), *args],
            cwd=servidor, env=env, capture_output=True, text=True, timeout=60,
        )

    class Ambiente:
        pass

    amb = Ambiente()
    amb.servidor, amb.publicar, amb.rodar = servidor, publicar, rodar
    amb.head = lambda: _git(servidor, "rev-parse", "HEAD")
    amb.chamadas = lambda: chamadas.read_text().splitlines()
    amb.log = lambda: (servidor / "var" / "atualizacao" / "atualizacao.log").read_text()
    return amb


def test_sem_novidade_nao_chama_docker(ambiente):
    antes = ambiente.head()
    resultado = ambiente.rodar()
    assert resultado.returncode == 0
    assert ambiente.head() == antes
    assert ambiente.chamadas() == []


def test_checar_nao_altera_nada(ambiente):
    antes = ambiente.head()
    ambiente.publicar("v2\n")
    resultado = ambiente.rodar("--checar")
    assert resultado.returncode == 0
    assert "atualização disponível" in ambiente.log()
    assert ambiente.head() == antes
    assert ambiente.chamadas() == []


def test_atualiza_testa_e_implanta_a_mesma_imagem(ambiente):
    novo = ambiente.publicar("v2\n")
    resultado = ambiente.rodar()
    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
    assert ambiente.head() == novo
    assert (ambiente.servidor / "app.txt").read_text() == "v2\n"
    chamadas = ambiente.chamadas()
    imagem = f"iativa-app:{novo[:12]}"
    assert chamadas[0] == f"build --build-arg IATIVA_VERSAO={novo[:12]} -t {imagem} ."
    assert chamadas[1].startswith(f"run --rm --network none --cpus 1.5 --memory 2g {imagem} pytest")
    assert chamadas[2] == f"implantar {imagem}"  # a imagem testada é a implantada
    assert f"OK atualizado para {novo[:7]}" in ambiente.log()


@pytest.mark.parametrize("etapa,implantar", [("build", "0"), ("pytest", "0"), ("", "1"), ("", "3")])
def test_falha_volta_o_codigo_sem_mexer_no_ar(ambiente, etapa, implantar):
    antes = ambiente.head()
    ambiente.publicar("v2\n")
    resultado = ambiente.rodar(falhar=etapa, implantar=implantar)
    assert resultado.returncode == 1
    assert ambiente.head() == antes
    assert (ambiente.servidor / "app.txt").read_text() == "v1\n"
    assert f"Código de volta para {antes[:7]}" in ambiente.log()
    if etapa:
        assert not any(c.startswith("implantar") for c in ambiente.chamadas())  # nada implantado
    if implantar == "3":
        assert "migração não aditiva" in ambiente.log()


def test_alteracao_local_bloqueia_atualizacao(ambiente):
    antes = ambiente.head()
    ambiente.publicar("v2\n")
    (ambiente.servidor / "app.txt").write_text("editado no servidor\n")
    resultado = ambiente.rodar()
    assert resultado.returncode == 3
    assert ambiente.head() == antes
    assert (ambiente.servidor / "app.txt").read_text() == "editado no servidor\n"
    assert ambiente.chamadas() == []


def test_branch_errada_bloqueia_atualizacao(ambiente):
    _git(ambiente.servidor, "checkout", "-q", "-b", "outra")
    resultado = ambiente.rodar()
    assert resultado.returncode == 2
    assert ambiente.chamadas() == []


def test_log_nao_contem_conteudo_do_env(ambiente):
    (ambiente.servidor / ".env").write_text("DJANGO_SECRET_KEY" + "=valor-que-nao-pode-vazar\n")
    with open(ambiente.servidor / ".git" / "info" / "exclude", "a") as f:
        f.write(".env\n")
    ambiente.publicar("v2\n")
    ambiente.rodar()
    assert "valor-que-nao-pode-vazar" not in ambiente.log()
