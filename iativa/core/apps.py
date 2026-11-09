from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "iativa.core"
    label = "iativa_core"
    verbose_name = "IAtiva — núcleo"

    def ready(self):
        from . import checks  # noqa: F401  (registra as verificações do sistema)
