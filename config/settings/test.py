"""Configurações dos testes automatizados: sem rede, sem segredos reais, dados sintéticos."""
import tempfile
from pathlib import Path

from .base import *  # noqa: F401,F403

SECRET_KEY = "chave-exclusiva-de-testes-automatizados-nao-usar-em-nenhum-ambiente-0123456789"

# SQLite é usado somente nos testes automatizados (decisão D2).
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}

SYNTHETIC_ERP_PATH = Path(tempfile.gettempdir()) / "iativa-testes-erp-sintetico.sqlite3"

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
