from django.shortcuts import render


def csrf_failure(request, reason=""):
    """Página genérica de falha de CSRF (sem detalhes técnicos)."""
    return render(request, "403_csrf.html", status=403)
