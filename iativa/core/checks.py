"""Verificações do sistema que impedem execução fora das regras da Etapa 1.

Executadas por `manage.py check`, `runserver`, `migrate` etc. Um erro aqui impede a inicialização.
"""
from pathlib import Path

from django.conf import settings
from django.core.checks import Error, register

PERMITTED_HOSTS = {"127.0.0.1", "localhost"}


@register()
def check_localhost_only(app_configs=None, **kwargs):
    errors = []
    hosts = set(settings.ALLOWED_HOSTS)
    if not hosts or not hosts <= PERMITTED_HOSTS:
        errors.append(
            Error(
                "ALLOWED_HOSTS deve conter somente 127.0.0.1 e/ou localhost nesta fase.",
                hint=f"Valor atual: {sorted(hosts)}. Acesso externo é a Etapa 9 e exige autorização.",
                id="iativa.E001",
            )
        )
    if settings.DEBUG:
        errors.append(
            Error(
                "DEBUG deve permanecer False.",
                hint="A página técnica de erro expõe variáveis locais e o ORM registraria SQL.",
                id="iativa.E002",
            )
        )
    return errors


@register()
def check_erp_separated_from_app_db(app_configs=None, **kwargs):
    erp_path = getattr(settings, "SYNTHETIC_ERP_PATH", None)
    if not erp_path:
        return [Error("SYNTHETIC_ERP_PATH não configurado.", id="iativa.E003")]
    default = settings.DATABASES.get("default", {})
    if default.get("ENGINE", "").endswith("sqlite3"):
        name = str(default.get("NAME", ""))
        if name and name != ":memory:" and Path(name).resolve() == Path(erp_path).resolve():
            return [
                Error(
                    "O banco da aplicação e a base do ERP (sintética) não podem ser o mesmo arquivo.",
                    id="iativa.E004",
                )
            ]
    return []


@register()
def check_secret_key(app_configs=None, **kwargs):
    key = getattr(settings, "SECRET_KEY", "") or ""
    if len(key) < 50 or key.startswith("django-insecure"):
        return [
            Error(
                "DJANGO_SECRET_KEY ausente, curta ou insegura.",
                hint='Gere com: python -c "import secrets; print(secrets.token_urlsafe(64))"',
                id="iativa.E005",
            )
        ]
    return []
