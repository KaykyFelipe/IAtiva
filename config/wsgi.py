"""Ponto WSGI. Não usar o servidor de desenvolvimento do Django em produção (ver Etapa 9)."""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
application = get_wsgi_application()
