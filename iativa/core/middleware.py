from django.conf import settings


class SecurityHeadersMiddleware:
    """Impede armazenamento das respostas (navegador, proxies) e aplica a CSP.

    Toda página pode, a partir da Etapa 5, conter resultados temporários; por isso a regra vale
    para todas as respostas da aplicação.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response["Cache-Control"] = "no-store, private"
        response["Pragma"] = "no-cache"
        response["Expires"] = "0"
        response.setdefault("Content-Security-Policy", settings.CONTENT_SECURITY_POLICY)
        return response
