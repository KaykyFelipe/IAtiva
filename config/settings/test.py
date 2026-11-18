"""Configurações dos testes automatizados: sem rede, sem segredos reais, dados sintéticos."""
import tempfile
from pathlib import Path

from .base import *  # noqa: F401,F403

SECRET_KEY = "chave-exclusiva-de-testes-automatizados-nao-usar-em-nenhum-ambiente-0123456789"

# SQLite é usado somente nos testes automatizados (decisão D2).
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}

SYNTHETIC_ERP_PATH = Path(tempfile.gettempdir()) / "iativa-testes-erp-sintetico.sqlite3"

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Testes antigos cobrem a manutenção automática ligada; os de M73 desligam explicitamente.
IATIVA_MANUTENCAO_AUTOMATICA = True

# M95: memória técnica sintética e índice/receitas em pasta temporária (nunca no repositório).
IATIVA_MEMORIA_TECNICA = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "memoria_protheus_sintetica.md"
IATIVA_MEMORIA_TECNICA_INDICE = Path(tempfile.gettempdir()) / "iativa-testes-memoria-indice"
IATIVA_RECEITAS_DIR = Path(tempfile.gettempdir()) / "iativa-testes-receitas"
