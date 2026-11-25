from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_not_required
from django.contrib.staticfiles.views import serve as servir_estatico
from django.urls import include, path, re_path
from iativa.agent import claude_views, codex_views, gemini_views
from iativa.core import saude

class EntrarView(auth_views.LoginView):
    """M88: ao entrar, sempre a página principal, sem conversa aberta (ignora o "next": a conversa pode ter
    sido excluída, ou o endereço guardado ser de uma ação de envio)."""

    def get_redirect_url(self):
        return ""


urlpatterns = [
    path("saude/pronto/", saude.pronto, name="saude-pronto"),
    path("saude/ativas/", saude.em_andamento, name="saude-ativas"),
    path("saude/lenta/", saude.lenta, name="saude-lenta"),
    # Estáticos servidos pela própria aplicação (cada versão serve os seus), como o runserver fazia.
    re_path(r"^static/(?P<path>.*)$", login_not_required(lambda r, path: servir_estatico(r, path, insecure=True))),
    path("configuracoes/claude/", claude_views.conta, name="claude-conta"),
    path("configuracoes/gemini/", gemini_views.conta, name="gemini-conta"),
    path("configuracoes/gemini/estado/<uuid:fluxo_id>/", gemini_views.estado, name="gemini-estado"),
    path("configuracoes/chatgpt/", codex_views.conta, name="chatgpt-conta"),
    path("configuracoes/chatgpt/estado/<uuid:fluxo_id>/", codex_views.estado, name="chatgpt-estado"),
    path("configuracoes/claude/estado/<uuid:fluxo_id>/", claude_views.estado, name="claude-estado"),
    path("", include("iativa.chat.urls")),
    path("entrar/", EntrarView.as_view(), name="login"),
    path("sair/", auth_views.LogoutView.as_view(), name="logout"),
    path("admin/", admin.site.urls),
]
