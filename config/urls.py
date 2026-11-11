from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from iativa.agent import claude_views

urlpatterns = [
    path("configuracoes/claude/", claude_views.conta, name="claude-conta"),
    path("configuracoes/claude/estado/<uuid:fluxo_id>/", claude_views.estado, name="claude-estado"),
    path("", include("iativa.chat.urls")),
    path("entrar/", auth_views.LoginView.as_view(), name="login"),
    path("sair/", auth_views.LogoutView.as_view(), name="logout"),
    path("admin/", admin.site.urls),
]
