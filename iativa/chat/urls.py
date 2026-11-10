from django.urls import path

from . import views

app_name = "chat"
urlpatterns = [
    path("", views.index, name="index"),
    path("nova/", views.nova, name="nova"),
    path("c/<int:conversa_id>/", views.conversa, name="conversa"),
    path("c/<int:conversa_id>/mensagem/", views.mensagem, name="mensagem"),
    path("c/<int:conversa_id>/parametro/", views.parametro, name="parametro"),
    path("c/<int:conversa_id>/cancelar/", views.cancelar, name="cancelar"),
    path("setor/<int:setor_id>/", views.setor, name="setor"),
    path("mestre/", views.mestre, name="mestre"),
]
