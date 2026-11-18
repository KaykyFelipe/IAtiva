"""M106: legendas de campos cujo nome do código o Protheus monta por rotina (X3_CBOX dinâmico, ex.: FP0_STATUS
por LOCA001CI) e que a empresa ensinou. Ficam numa nota da empresa aprovada (NotaAgente, tipo termo) com
`criterios = {"legenda": {"tabela", "campo", "opcoes": {codigo: nome}}}`; o catálogo as usa como domínio do campo."""


def legendas_aprendidas(alias: str) -> dict[str, dict[str, str]]:
    from .models import EscopoNota, NotaAgente, SituacaoNota, TipoNota
    out = {}
    notas = NotaAgente.objects.filter(ativo=True, escopo=EscopoNota.EMPRESA, situacao=SituacaoNota.APROVADA,
                                      tipo=TipoNota.TERMO).order_by("pk").values_list("criterios", flat=True)
    for criterios in notas:
        leg = (criterios or {}).get("legenda") if isinstance(criterios, dict) else None
        if not isinstance(leg, dict) or leg.get("tabela") != alias or not isinstance(leg.get("opcoes"), dict):
            continue
        opcoes = {str(k).strip(): str(v).strip() for k, v in leg["opcoes"].items() if str(k).strip() and str(v).strip()}
        if opcoes and leg.get("campo"):
            out[str(leg["campo"])] = opcoes
    return out


def gravar_legenda(alias: str, campo: str, opcoes: dict, autor=None):
    """Grava (ou substitui) a legenda aprendida de um campo, como nota aprovada da empresa."""
    from django.utils import timezone
    from .models import EscopoNota, NotaAgente, SituacaoNota, TipoNota
    texto = f"Legenda de {campo}: " + "; ".join(f"{k}={v}" for k, v in opcoes.items())
    for n in NotaAgente.objects.filter(ativo=True, escopo=EscopoNota.EMPRESA, tipo=TipoNota.TERMO):
        leg = (n.criterios or {}).get("legenda") if isinstance(n.criterios, dict) else None
        if isinstance(leg, dict) and leg.get("tabela") == alias and leg.get("campo") == campo:
            n.ativo = False
            n.situacao = SituacaoNota.SUBSTITUIDA
            n.save(update_fields=["ativo", "situacao"])
    return NotaAgente.objects.create(
        texto=texto[:300], escopo=EscopoNota.EMPRESA, situacao=SituacaoNota.APROVADA, tipo=TipoNota.TERMO,
        criterios={"legenda": {"tabela": alias, "campo": campo, "opcoes": dict(opcoes)}},
        criado_por=autor, aprovado_por=autor, aprovado_em=timezone.now())
