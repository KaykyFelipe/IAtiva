from django.urls import path


def view_com_erro(request):
    VALOR_SECRETO_LOCAL = "SELECT * FROM SE1010"  # noqa: F841 - não pode aparecer na página de erro
    raise RuntimeError("falha proposital de teste")


urlpatterns = [path("erro/", view_com_erro)]
