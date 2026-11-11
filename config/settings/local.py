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

# ERP real (SQL Server). Só preenchido no .env do servidor; sem IATIVA_ERP_HOST fica desligado.
IATIVA_ERP = (
    {
        "host": env("IATIVA_ERP_HOST"),
        "porta": env("IATIVA_ERP_PORT", "1433"),
        "banco": env_required("IATIVA_ERP_DATABASE"),
        "usuario": env_required("IATIVA_ERP_USER"),
        "senha": env_required("IATIVA_ERP_PASSWORD"),
        "criptografar": env("IATIVA_ERP_ENCRYPT", "yes"),
        "confiar_certificado": env("IATIVA_ERP_TRUST_CERT", "no"),
    }
    if env("IATIVA_ERP_HOST")
    else None
)

# Fonte de metadados: com o ERP configurado, usa o Protheus real; sem ele, a base sintética.
# IATIVA_METADADOS_FONTE no .env força uma das duas (sintetico | protheus).
IATIVA_METADADOS = {"fonte": env("IATIVA_METADADOS_FONTE", "protheus" if IATIVA_ERP else "sintetico")}

IATIVA_AGENTE = {
    **IATIVA_AGENTE,  # noqa: F405
    "provedor": env("IATIVA_AGENTE_PROVEDOR", "teste"),
    "modelo": env("IATIVA_AGENTE_MODELO", "claude-opus-5-5"),
    "api_key": env("ANTHROPIC_API_KEY"),
    "gemini_api_key": env("GEMINI_API_KEY"),
    "gemini_modelo": env("IATIVA_GEMINI_MODELO", "gemini-3.8-flash"),
    "envio_liberado": env("IATIVA_AGENTE_ENVIO_LIBERADO", "0") == "1",
}

IATIVA_APRENDIZADO_AUTOAPROVACAO = env("IATIVA_APRENDIZADO_AUTOAPROVACAO", "0") == "1"

SYNTHETIC_ERP_PATH = Path(env("IATIVA_SYNTHETIC_ERP_PATH", str(BASE_DIR / "var" / "erp_sintetico.sqlite3")))
