from .models import OperationAudit


def registrar(
    acao: str,
    entidade: str,
    *,
    usuario=None,
    setor=None,
    entidade_id: int | None = None,
    estado_anterior: str = "",
    estado_novo: str = "",
    resultado: str = "ok",
    duracao_ms: int | None = None,
    linhas: int | None = None,
) -> OperationAudit:
    """Grava um registro de auditoria sanitizado (somente códigos e contagens)."""
    return OperationAudit.objects.create(
        acao=acao,
        entidade=entidade,
        usuario=usuario,
        setor=setor,
        entidade_id=entidade_id,
        estado_anterior=estado_anterior,
        estado_novo=estado_novo,
        resultado=resultado,
        duracao_ms=duracao_ms,
        linhas=linhas,
    )
