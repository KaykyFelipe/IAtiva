"""Proteção de credenciais: nada de segredo no repositório; configuração obrigatória via .env."""
import os
import re
import subprocess
import sys
from pathlib import Path

from django.conf import settings

RAIZ = Path(settings.BASE_DIR)
IGNORAR = {".git", ".venv", "var", "staticfiles", "__pycache__", ".pytest_cache", "vendor"}


def test_env_esta_no_gitignore():
    linhas = (RAIZ / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in linhas
    assert "var/" in linhas


def test_env_example_sem_valores_secretos():
    for linha in (RAIZ / ".env.example").read_text(encoding="utf-8").splitlines():
        if linha.startswith(("DJANGO_SECRET_KEY=", "IATIVA_DB_PASSWORD=")):
            assert linha.split("=", 1)[1] == ""


def test_nenhum_segredo_versionado():
    padrao = re.compile(r"(DJANGO_SECRET_KEY|IATIVA_DB_PASSWORD)[ \t]*=[ \t]*[^\s\"',)]+|PWD=\w{6,}")
    suspeitos = []
    for arquivo in RAIZ.rglob("*"):
        if not arquivo.is_file() or IGNORAR & set(arquivo.relative_to(RAIZ).parts):
            continue
        if arquivo.name in {".env"} or arquivo.parts[-2:] in {("tests", "test_logs.py"), ("tests", "test_segredos.py")}:
            continue
        try:
            texto = arquivo.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if padrao.search(texto):
            suspeitos.append(str(arquivo.relative_to(RAIZ)))
    assert suspeitos == []


def test_configuracao_local_exige_chave_secreta(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DJANGO_", "IATIVA_"))}
    env["DJANGO_SETTINGS_MODULE"] = "config.settings.local"
    codigo = (
        "import django, config.settings._env as e; e.load_env_file = lambda p: None; "
        "django.setup()"
    )
    resultado = subprocess.run(
        [sys.executable, "-c", codigo], cwd=RAIZ, env=env, capture_output=True, text=True, timeout=60
    )
    assert resultado.returncode != 0
    assert "DJANGO_SECRET_KEY" in resultado.stderr
