"""Leitura mínima de variáveis de ambiente e do arquivo .env (sem dependências externas)."""
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured


def load_env_file(path: Path) -> None:
    """Carrega KEY=VALUE de um arquivo .env sem sobrescrever variáveis já definidas."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def env(name: str, default: str = "") -> str:
    value = os.environ.get(name, "")
    return value if value != "" else default


def env_required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise ImproperlyConfigured(
            f"A variável de ambiente {name} é obrigatória. Defina-a no arquivo .env (veja .env.example)."
        )
    return value
