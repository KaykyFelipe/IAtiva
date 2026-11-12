from django.apps import AppConfig


class ManutencaoConfig(AppConfig):
    name = "iativa.manutencao"
    label = "iativa_manutencao"
    verbose_name = "IAtiva — manutenção (incidentes)"

    def ready(self):
        from django.core.signals import got_request_exception

        from . import incidentes

        got_request_exception.connect(incidentes.ao_erro_de_requisicao, dispatch_uid="iativa_incidente_requisicao")
