from django.urls import path

from . import views

app_name = "chat"
urlpatterns = [
    path("", views.index, name="index"),
    path("parametro/", views.parametro, name="parametro"),
    path("cancelar/", views.cancelar_atual, name="cancelar"),
]
