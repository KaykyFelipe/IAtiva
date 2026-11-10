"""Descoberta progressiva de metadados (PEDIDO_INICIAL §6) com restrição de acesso (§10).

Etapas, cada uma pequena e auditada:
1. `dominios_disponiveis` — domínios que o usuário pode usar.
2. `entidades` — tabelas do domínio (sem campos).
3. `detalhar` — campos de poucas tabelas por vez.
4. `relacionamentos` — ligações aprovadas no dicionário entre tabelas já escolhidas.
5. `conhecimento` — trechos de documentos aprovados relevantes para a pergunta.

Acesso: o usuário precisa de `pode_consultar` em um setor habilitado para o domínio
(`DominioSetor`). Tabelas e campos com `RestricaoMetadado` só aparecem para os setores
permitidos. A auditoria guarda só a etapa e a contagem, nunca termos da pergunta.
"""
from django.conf import settings

from iativa.accounts.authz import Permissao, PermissaoNegada, setor_do_usuario, setores_autorizados
from iativa.accounts.models import Department
from iativa.audit.servicos import registrar
from iativa.knowledge.busca import Trecho, buscar
from iativa.knowledge.documentos import carregar_todos

from .catalogo import abrir_catalogo
from .dominios import DOMINIOS
from .fontes import FonteInvalida
from .models import DominioSetor, RestricaoMetadado
from .tipos import Relacionamento, Tabela

MAX_TABELAS_POR_DETALHE = 3


def _negar(user, motivo: str):
    registrar("acesso_negado", "metadado", usuario=user, setor=setor_do_usuario(user),
              estado_novo=motivo, resultado="negado")
    raise PermissaoNegada("Sem acesso a este domínio ou entidade.")


def _auditar(user, acao: str, etapa: str, linhas: int):
    registrar(acao, "conhecimento" if acao == "conhecimento_consultado" else "metadado",
              usuario=user, setor=setor_do_usuario(user), estado_novo=etapa, linhas=linhas)


def setores_do_dominio(user, dominio: str) -> set[int]:
    if dominio not in DOMINIOS:
        return set()
    habilitados = set(
        DominioSetor.objects.filter(dominio=dominio, ativo=True, setor__ativo=True).values_list("setor_id", flat=True)
    )
    return habilitados & setores_autorizados(user, Permissao.CONSULTAR)


def _exigir_dominio(user, dominio: str) -> set[int]:
    setores = setores_do_dominio(user, dominio)
    if not setores:
        _negar(user, "dominio")
    return setores


class _Visibilidade:
    def __init__(self, setores: set[int], aliases):
        self.ocultas_tabela, self.ocultos_campo = set(), set()
        for r in RestricaoMetadado.objects.filter(ativo=True, tabela__in=list(aliases)).prefetch_related(
            "setores_permitidos"
        ):
            if setores & {s.pk for s in r.setores_permitidos.all()}:
                continue
            if r.campo:
                self.ocultos_campo.add((r.tabela, r.campo))
            else:
                self.ocultas_tabela.add(r.tabela)

    def tabela(self, alias) -> bool:
        return alias not in self.ocultas_tabela

    def campo(self, alias, campo) -> bool:
        return self.tabela(alias) and (alias, campo) not in self.ocultos_campo


def dominios_disponiveis(user) -> list[dict]:
    out = [{"codigo": c, "nome": d["nome"]} for c, d in DOMINIOS.items() if setores_do_dominio(user, c)]
    _auditar(user, "metadado_consultado", "dominios", len(out))
    return out


def _tabelas_visiveis(user, dominio) -> tuple[set[int], _Visibilidade, list[str]]:
    setores = _exigir_dominio(user, dominio)
    aliases = list(DOMINIOS[dominio]["tabelas"])
    vis = _Visibilidade(setores, aliases)
    return setores, vis, [a for a in aliases if vis.tabela(a)]


def entidades(user, dominio: str) -> list[Tabela]:
    _, _, aliases = _tabelas_visiveis(user, dominio)
    with abrir_catalogo() as cat:
        tabelas = list(cat.tabelas(aliases).values())
    _auditar(user, "metadado_consultado", "entidades", len(tabelas))
    return tabelas


def detalhar(user, dominio: str, aliases: list[str], campos: list[str] | None = None) -> list[Tabela]:
    _, vis, visiveis = _tabelas_visiveis(user, dominio)
    pedidas = list(dict.fromkeys(a.upper() for a in aliases))
    if not pedidas or len(pedidas) > MAX_TABELAS_POR_DETALHE:
        raise FonteInvalida(f"Detalhe de 1 a {MAX_TABELAS_POR_DETALHE} tabelas por vez.")
    if any(a not in visiveis for a in pedidas):
        _negar(user, "entidade")
    filtro = {c.upper() for c in campos} if campos else None
    with abrir_catalogo() as cat:
        tabelas = cat.tabelas(pedidas, com_campos=True)
    out, total = [], 0
    for alias in pedidas:
        t = tabelas.get(alias)
        if t is None:
            continue
        mantidos = tuple(
            c for c in t.campos if vis.campo(alias, c.nome) and (filtro is None or c.nome in filtro)
        )
        total += len(mantidos)
        out.append(Tabela(**{**t.__dict__, "campos": mantidos}))
    _auditar(user, "metadado_consultado", "campos", total)
    return out


def relacionamentos(user, dominio: str, aliases: list[str]) -> list[Relacionamento]:
    _, vis, visiveis = _tabelas_visiveis(user, dominio)
    pedidas = [a.upper() for a in aliases if a.upper() in visiveis]
    with abrir_catalogo() as cat:
        rels = cat.relacionamentos(pedidas)
    out = [
        r for r in rels
        if all(vis.campo(r.origem, c) for c in r.campos_origem) and all(vis.campo(r.destino, c) for c in r.campos_destino)
    ]
    _auditar(user, "metadado_consultado", "relacionamentos", len(out))
    return out


def conhecimento(user, dominio: str, consulta: str, limite: int = 3) -> list[Trecho]:
    setores, vis, _ = _tabelas_visiveis(user, dominio)
    codigos = set(Department.objects.filter(pk__in=setores).values_list("codigo", flat=True))
    sintetico = settings.IATIVA_METADADOS["fonte"] == "sintetico"
    docs = [
        d for d in carregar_todos()
        if d.recuperavel
        and d.dominio == dominio
        and (d.origem == "sintetico") == sintetico  # nunca mistura conhecimento sintético com o real
        and (not d.setores or codigos & set(d.setores))
        and all(vis.tabela(t) for t in d.tabelas)
    ]
    trechos = buscar(consulta, docs, limite)
    _auditar(user, "conhecimento_consultado", "trechos", len(trechos))
    return trechos
