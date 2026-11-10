"""Configurações comuns do IAtiva (Etapa 1 — somente localhost).

Regras aplicadas aqui (ver docs/PEDIDO_INICIAL.md e DECISOES.md):
- Acesso exclusivo por 127.0.0.1 / localhost.
- DEBUG fixo em False: a página técnica de erro do Django exibiria variáveis locais
  (que no futuro podem conter SQL ou resultados) e o log de SQL do ORM.
- Nenhum cache de respostas; cabeçalho Cache-Control: no-store em todas as páginas.
- Logs passam por filtro de redação.
"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]

DEBUG = False

LOCALHOST_HOSTS = ("127.0.0.1", "localhost")
ALLOWED_HOSTS = list(LOCALHOST_HOSTS)

INSTALLED_APPS = [
    # iativa.core vem antes de staticfiles para substituir o comando runserver.
    "iativa.core",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "iativa.accounts",
    "iativa.chat",
    "iativa.memory",
    "iativa.audit",
    "iativa.knowledge",
    "iativa.metadata",
    "iativa.synthetic_erp",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.auth.middleware.LoginRequiredMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "iativa.core.middleware.SecurityHeadersMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# Autenticação
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "chat:index"
LOGOUT_REDIRECT_URL = "login"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Sessão e cookies (HTTP em localhost; cookies Secure serão exigidos na Etapa 9, com HTTPS).
SESSION_COOKIE_NAME = "iativa_sessao"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Strict"
SESSION_COOKIE_AGE = 8 * 60 * 60
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
CSRF_COOKIE_NAME = "iativa_csrf"
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Strict"
CSRF_FAILURE_VIEW = "iativa.core.views.csrf_failure"

# Cabeçalhos de segurança
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
    "connect-src 'self'; form-action 'self'; frame-ancestors 'none'; "
    "base-uri 'none'; object-src 'none'"
)

# Nenhum cache de aplicação (resultados nunca devem ser armazenados em cache).
CACHES = {"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}}

# Sem envio de e-mails de erro (poderiam carregar dados da requisição).
ADMINS = []
MANAGERS = []

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Limite de tamanho da mensagem do chat (caracteres).
IATIVA_CHAT_MAX_CHARS = 2000

# Retenção (D11, PLANO.md §4). None = prazo "a definir": nada é removido automaticamente.
IATIVA_RETENCAO = {
    "solicitacao_dias": 90,
    "valor_dado_pessoal_dias": 30,
    "auditoria_dias": None,
}

# Etapa 3 — conhecimento aprovado e fonte de metadados. Só a fonte sintética está liberada (R5);
# o Protheus real exige liberação explícita (DECISOES.md) e as informações 4–8 do STATUS.md.
IATIVA_CONHECIMENTO_DIR = BASE_DIR / "knowledge"
IATIVA_METADADOS = {"fonte": "sintetico"}
# Empresa do ERP real (sufixo das tabelas: SE1010, SX2010...). Confirmada em 2026-11-10.
IATIVA_ERP_SUFIXO = "010"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "redact": {"()": "iativa.core.logging_filters.RedactSensitiveFilter"},
    },
    "formatters": {
        "simple": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "filters": ["redact"],
            "formatter": "simple",
        },
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "django.server": {"handlers": ["console"], "level": "INFO", "propagate": False},
        # Nunca registrar SQL executado pelo ORM.
        "django.db.backends": {"handlers": [], "level": "CRITICAL", "propagate": False},
    },
}
