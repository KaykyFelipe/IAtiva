from django.apps import AppConfig


class AgentConfig(AppConfig):
    name = "iativa.agent"
    label = "iativa_agent"
    verbose_name = "IAtiva — agente"

    def ready(self):
        from . import sinais_receitas  # noqa: F401  (M95: Markdown das receitas acompanha o catálogo)
