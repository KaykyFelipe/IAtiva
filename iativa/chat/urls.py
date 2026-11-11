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
    path("c/<int:conversa_id>/executar/", views.executar, name="executar"),
    path("c/<int:conversa_id>/executar/<int:solicitacao_id>/de-novo/", views.executar_de_novo, name="executar_de_novo"),
    path("c/<int:conversa_id>/aprender/", views.aprender, name="aprender"),
    path("c/<int:conversa_id>/aprender/salvar/", views.aprender_salvar, name="aprender_salvar"),
    path("c/<int:conversa_id>/aprender/<int:modelo_id>/", views.aprender_decidir, name="aprender_decidir"),
    path("setor/<int:setor_id>/", views.setor, name="setor"),
    path("mestre/", views.mestre, name="mestre"),
    path("memoria/", views.memoria, name="memoria"),
    path("memoria/decidir/", views.memoria_decidir, name="memoria_decidir"),
]
