from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_not_required
from django.contrib.staticfiles.views import serve as servir_estatico
from django.urls import include, path, re_path
from iativa.agent import claude_views
from iativa.core import saude

urlpatterns = [
    path("saude/pronto/", saude.pronto, name="saude-pronto"),
    path("saude/ativas/", saude.em_andamento, name="saude-ativas"),
    path("saude/lenta/", saude.lenta, name="saude-lenta"),
    # Estáticos servidos pela própria aplicação (cada versão serve os seus), como o runserver fazia.
    re_path(r"^static/(?P<path>.*)$", login_not_required(lambda r, path: servir_estatico(r, path, insecure=True))),
    path("configuracoes/claude/", claude_views.conta, name="claude-conta"),
    path("configuracoes/claude/estado/<uuid:fluxo_id>/", claude_views.estado, name="claude-estado"),
    path("", include("iativa.chat.urls")),
    path("entrar/", auth_views.LoginView.as_view(), name="login"),
    path("sair/", auth_views.LogoutView.as_view(), name="logout"),
    path("admin/", admin.site.urls),
]
