"""Configurações para execução local (localhost). Valores sensíveis vêm do .env."""
from pathlib import Path

from ._env import env, env_required, load_env_file
from .base import *  # noqa: F401,F403
from .base import BASE_DIR

load_env_file(BASE_DIR / ".env")

SECRET_KEY = env_required("DJANGO_SECRET_KEY")

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("IATIVA_DB_NAME", "iativa"),
        "USER": env("IATIVA_DB_USER", "iativa"),
        "PASSWORD": env_required("IATIVA_DB_PASSWORD"),
        "HOST": env("IATIVA_DB_HOST", "127.0.0.1"),
        "PORT": env("IATIVA_DB_PORT", "5432"),
        "CONN_MAX_AGE": 0,
        "OPTIONS": {"application_name": "iativa"},
    }
}

SYNTHETIC_ERP_PATH = Path(env("IATIVA_SYNTHETIC_ERP_PATH", str(BASE_DIR / "var" / "erp_sintetico.sqlite3")))
