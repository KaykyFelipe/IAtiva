from django.apps import AppConfig


class ChatConfig(AppConfig):
    name = "iativa.chat"
    label = "iativa_chat"
    verbose_name = "IAtiva — chat"

    def ready(self):
        from . import erros  # noqa: F401  (M149: registra os erros da IA ao salvar a resposta)
