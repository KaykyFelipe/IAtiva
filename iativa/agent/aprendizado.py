"""Aprendizado assistido de novas consultas (M9).

1. `candidatos`: busca no dicionário (SX2/SX3) as tabelas ligadas aos termos da pergunta.
2. `campos_da_tabela`: campos consultáveis da tabela escolhida (para o formulário).
3. `salvar_proposta`: valida a escolha do administrador contra o catálogo, testa a compilação
   (sem executar) e grava a consulta-modelo como proposta.
4. `aprovar` / `rejeitar`: quem tem "aprovar definições" libera ou recusa.

Só administradores aprendem. Nada de SQL livre: a consulta-modelo é estrutura e passa pelo mesmo
compilador e validador das consultas fixas.
"""
import re
import unicodedata

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from iativa.accounts.authz import Permissao, PermissaoNegada, politicas_vigentes, tem_permissao
from iativa.accounts.models import Department
from iativa.audit.servicos import registrar
from iativa.metadata.catalogo import abrir_catalogo
from iativa.metadata.fontes import ALIAS_RE, CAMPO_RE
from iativa.metadata.models import RestricaoMetadado

from .contratos import MAX_FILTRAVEIS, PREFIXO_APRENDIDO
from .models import ConsultaModelo, SituacaoModelo

MAX_COLUNAS = 15
MAX_FILTROS = 3
VALOR_FIXO_RE = re.compile(r"^[0-9A-Za-z \-]{1,40}$")
IGNORAR = {"QUAIS", "QUAL", "QUANTOS", "QUANTAS", "DIAS", "ULTIMOS", "ULTIMAS", "MATRIZ",
           "FILIAL", "LISTA", "LISTAR", "MOSTRE", "MOSTRAR", "PARA", "COM", "SEM", "QUE", "FORAM", "ESTAO",
           "TODOS", "TODAS", "MINHA", "NOSSA", "ESSE", "ESSA", "ENTRE", "DESDE", "PERIODO"}
SISTEMA = ("SX", "SIX", "XX", "SYS", "SM0")


def pode_aprender(user) -> bool:
    return politicas_vigentes(user).filter(pode_administrar=True).exists()


def pode_aprender_sozinho(user) -> bool:
    """M18: a IA cria consultas sozinha para quem consulta algum setor (sem aprovação humana)."""
    from iativa.accounts.authz import Permissao, setores_autorizados

    return pode_aprender(user) or bool(setores_autorizados(user, Permissao.CONSULTAR))


# Campos que a criação automática nunca mostra (documentos, salário, dados pessoais e bancários).
SENSIVEL_RE = re.compile(r"CPF|CGC|CNPJ|\bRG\b|_RG\b|PIS\b|_PIS|CTPS|SALAR|SAL[AÁ]RIO|SENHA|NASC|\bM[AÃ]E\b|\bPAI\b|"
                         r"BANCO|AGENC|CONTA ?COR|E-?MAIL|TELEF|FONE|CELUL|ENDERE|BAIRRO|\bCEP\b|_CIC\b", re.I)


def sensivel(campo) -> bool:
    return bool(SENSIVEL_RE.search(f"{campo.nome} {campo.titulo or ''} {campo.descricao or ''}"))

def _normalizar(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode().upper()


def _radical(palavra: str) -> str:
    """Radical leve em português: particípio "-tido(s)" vira "-ss" para casar com o substantivo
    ("DEMITIDAS"→"DEMISS" ~ "DEMISSAO", "EMITIDOS"→"EMISS" ~ "EMISSAO"); depois 5 letras."""
    p = re.sub(r"TID[OA]S?$", "SS", palavra)
    p = re.sub(r"S$", "", p) if len(p) > 5 else p
    return p[:5]


# Sinônimos de negócio (radical → radical usado no dicionário do Protheus).
SINONIMOS = {"PESSO": "FUNCI", "COLAB": "FUNCI", "EMPRE": "FUNCI", "TRABA": "FUNCI"}


def _radicais(texto: str) -> set[str]:
    radicais = {_radical(p) for p in re.findall(r"[A-Z]+", _normalizar(texto)) if len(p) >= 5 and p not in IGNORAR}
    return {SINONIMOS.get(r, r) for r in radicais}


def _aliases_citados(texto: str) -> list[str]:
    # Só o que o usuário escreveu em maiúsculas (ex.: "SRA"), para não confundir "com", "dia"...
    return [p for p in re.findall(r"\b[A-Z][A-Z0-9]{2}\b", texto or "") if ALIAS_RE.match(p)]


def _restritas(user) -> set[str]:
    from iativa.accounts.authz import setor_do_usuario

    setor = setor_do_usuario(user)
    out = set()
    for r in RestricaoMetadado.objects.filter(ativo=True, campo="").prefetch_related("setores_permitidos"):
        if setor is None or setor.pk not in {s.pk for s in r.setores_permitidos.all()}:
            out.add(r.tabela)
    return out


def candidatos(user, texto: str, limite: int = 5, automatico: bool = False) -> list[dict]:
    if not (pode_aprender_sozinho(user) if automatico else pode_aprender(user)):
        raise PermissaoNegada("Somente administradores ensinam novas consultas.")
    radicais = _radicais(texto)
    citados = _aliases_citados(texto)
    with abrir_catalogo() as cat:
        tabelas, campos = cat.dicionario.indice()
        pontos: dict[str, dict] = {}

        def item(alias):
            return pontos.setdefault(alias, {"alias": alias, "pontos": 0, "termos": set(), "campos": []})

        for alias, nome in tabelas:
            achados = radicais & _radicais(nome)
            if achados:
                i = item(alias)
                i["termos"] |= achados
                i["pontos"] += 3 * len(achados)
        for alias, campo, titulo, desc in campos:
            achados = radicais & (_radicais(titulo) | _radicais(desc))
            if achados:
                i = item(alias)
                i["termos"] |= achados
                i["pontos"] += 1
                if len(i["campos"]) < 5:
                    i["campos"].append({"campo": campo, "titulo": titulo})
        for alias in citados:
            item(alias)["pontos"] += 100
        for i in pontos.values():
            i["pontos"] += 20 * len(i["termos"])  # cobrir mais termos da pergunta vale mais
        restritas = _restritas(user)
        validos = [i for i in pontos.values()
                   if ALIAS_RE.match(i["alias"]) and not i["alias"].startswith(SISTEMA) and i["alias"] not in restritas]
        validos.sort(key=lambda i: (-i["pontos"], i["alias"]))
        escolhidos = validos[:limite]
        existentes = cat.dicionario.tabelas([i["alias"] for i in escolhidos])
    out = []
    for i in escolhidos:
        td = existentes.get(i["alias"])
        if td is None:
            continue
        out.append({"alias": i["alias"], "nome": td.nome, "campos": i["campos"]})
    registrar("metadado_consultado", "metadado", usuario=user, estado_novo="aprendizado", linhas=len(out))
    return out


def campos_da_tabela(user, alias: str, automatico: bool = False) -> dict:
    if not (pode_aprender_sozinho(user) if automatico else pode_aprender(user)):
        raise PermissaoNegada("Somente administradores ensinam novas consultas.")
    if not ALIAS_RE.match(alias or "") or alias.startswith(SISTEMA) or alias in _restritas(user):
        raise PermissaoNegada("Tabela indisponível.")
    with abrir_catalogo() as cat:
        t = cat.tabelas([alias], com_campos=True).get(alias)
    if t is None:
        raise ValidationError("Tabela não encontrada no dicionário.")
    if not t.campo_filial:
        raise ValidationError("A tabela não tem campo de filial; por segurança, não pode ser aprendida.")
    campos = [c for c in t.campos if c.consultavel and c.nome != t.campo_filial]
    return {
        "alias": t.alias, "nome": t.nome, "fisica": t.fisica, "campo_filial": t.campo_filial,
        "campos": campos, "datas": [c for c in campos if c.tipo == "D"],
        "textos": [c for c in campos if c.tipo == "C"],
        "setores": Department.objects.filter(ativo=True).order_by("nome"),
    }


def _tipo(campo) -> str:
    return {"D": "data", "N": "numero"}.get(campo.tipo, "texto")


MAX_JUNCOES = 2
MAX_COLUNAS_JUNCAO = 5


def juncoes_possiveis(user, alias: str) -> list[dict]:
    """Tabelas que a base `alias` referencia pelo dicionário (SX9) e que dá para juntar sem duplicar:
    os campos do outro lado cobrem a chave única dele (SIX ordem 1, sem a filial). Ex.: SF2 -> SA1
    por F2_CLIENTE+F2_LOJA = A1_COD+A1_LOJA, para trazer o nome do cliente."""
    restritas = _restritas(user)
    out, vistos = [], set()
    with abrir_catalogo() as cat:
        # Duas ligações para a mesma tabela (ex.: SF2 -> SA1 pelo cliente da nota e pelo remetente):
        # vale primeiro a que usa campos da chave da própria base, que é a ligação principal.
        base = cat.tabelas([alias], com_campos=True).get(alias)
        chave_base = set(base.chave) if base is not None and base.chave else set()

        def principal(r):
            campos = [b for b in r.campos_destino if not b.endswith("_FILIAL")]
            return 0 if campos and set(campos) <= chave_base else 1

        for r in sorted(cat.relacionados(alias), key=principal):
            dom = r.origem  # tabela referenciada (X9_DOM)
            if dom in vistos or not ALIAS_RE.match(dom) or dom.startswith(SISTEMA) or dom in restritas:
                continue
            t = cat.tabelas([dom], com_campos=True).get(dom)
            if t is None or not t.chave:
                continue
            pares = [(a, b) for a, b in zip(r.campos_origem, r.campos_destino)
                     if not a.endswith("_FILIAL") and not b.endswith("_FILIAL")]
            chave = {c for c in t.chave if not c.endswith("_FILIAL")}
            if not pares or not chave or not chave <= {a for a, _ in pares}:
                continue  # não liga pela chave única: poderia duplicar linhas e somas
            vistos.add(dom)
            campos = [c for c in t.campos if c.consultavel and not c.nome.endswith("_FILIAL") and not sensivel(c)]
            out.append({"tabela": dom, "nome": t.nome, "pares": [list(p) for p in pares], "campos": campos})
    return out


@transaction.atomic
def criar_automatico(user, consulta: dict) -> ConsultaModelo:
    """M18: a IA montou a consulta; o sistema confere, cria e já aprova (sem revisão humana).
    Colunas sensíveis são descartadas; setores = os que a pessoa consulta."""
    from iativa.accounts.authz import Permissao, setores_autorizados

    alias = consulta["tabela"]
    info = campos_da_tabela(user, alias, automatico=True)
    por_nome = {c.nome: c for c in info["campos"] if not sensivel(c)}
    colunas = [c for c in dict.fromkeys(consulta["colunas"]) if c in por_nome][:MAX_COLUNAS]
    if not colunas:
        raise ValidationError("Nenhuma coluna válida para a consulta.")
    busca = consulta.get("campo_busca", "")
    filtraveis = [c for c in consulta.get("filtraveis", []) if c in por_nome and por_nome[c].tipo == "C"]
    dados = {
        "nome": consulta["nome"] or info["nome"], "descricao": consulta.get("descricao", ""),
        "colunas": colunas,
        "campo_data": consulta.get("campo_data", "") if por_nome.get(consulta.get("campo_data", "")) else "",
        "campo_busca": busca if busca in por_nome and por_nome[busca].tipo == "C" else "",
        "filtraveis": [c for c in filtraveis if c in colunas or len(colunas) < MAX_COLUNAS][:MAX_FILTRAVEIS],
        "setores": [str(s) for s in setores_autorizados(user, Permissao.CONSULTAR)],
    }
    if not dados["setores"]:
        raise PermissaoNegada("Sem setor para usar a consulta.")
    igual = ConsultaModelo.objects.filter(tabela=alias, nome=dados["nome"], situacao=SituacaoModelo.APROVADA).first()
    if igual is not None:
        return igual
    cm = salvar_proposta(user, alias, dados, automatico=True)
    pedidas = consulta.get("juncoes") or []
    if pedidas:
        possiveis = {j["tabela"]: j for j in juncoes_possiveis(user, alias)}
        juncoes, extras = [], []
        for n, pedida in enumerate(pedidas[:MAX_JUNCOES], start=1):
            j = possiveis.get(pedida.get("tabela", ""))
            if j is None:
                continue  # sem relacionamento seguro: ignora em vez de arriscar duplicar
            por_nome_j = {c.nome: c for c in j["campos"]}
            cols = [c for c in dict.fromkeys(pedida.get("colunas", [])) if c in por_nome_j][:MAX_COLUNAS_JUNCAO]
            if not cols:
                continue
            apelido = f"j{n}"
            juncoes.append({"tabela": j["tabela"], "alias": apelido, "pares": j["pares"]})
            extras += [{"campo": c, "rotulo": por_nome_j[c].titulo or c, "tipo": _tipo(por_nome_j[c]),
                        "alias": apelido} for c in cols]
        if juncoes:
            spec = dict(cm.spec)
            spec["juncoes"] = juncoes
            spec["colunas"] = spec["colunas"] + extras
            cm.spec = spec
            cm.save(update_fields=["spec"])
            _testar_compilacao(cm)
    cm.situacao, cm.aprovado_por, cm.aprovado_em = SituacaoModelo.APROVADA, user, timezone.now()
    cm.save(update_fields=["situacao", "aprovado_por", "aprovado_em"])
    registrar("parametro_aprovado", "agente", usuario=user, entidade_id=cm.pk, estado_novo="modelo_aprovado_automatico")
    return cm


@transaction.atomic
def salvar_proposta(user, alias: str, dados, automatico: bool = False) -> ConsultaModelo:
    """`dados`: QueryDict/dict com nome, descricao, colunas (lista), campo_data, filtro_campo_N,
    filtro_valor_N, setores (lista de ids)."""
    info = campos_da_tabela(user, alias, automatico=automatico)
    por_nome = {c.nome: c for c in info["campos"]}
    lista = dados.getlist if hasattr(dados, "getlist") else (lambda k: dados.get(k, []))
    nome = (dados.get("nome") or "").strip()[:120]
    if not nome:
        raise ValidationError("Dê um nome para a consulta.")
    colunas = [c for c in lista("colunas") if c in por_nome]
    # Campos de busca e filtráveis precisam aparecer no resultado: entram nas colunas sozinhos.
    for extra in [dados.get("campo_busca") or ""] + list(lista("filtraveis")):
        if extra in por_nome and extra not in colunas:
            colunas.append(extra)
    colunas = colunas[:MAX_COLUNAS + 1]
    if not colunas:
        raise ValidationError("Escolha ao menos uma coluna.")
    if len(colunas) > MAX_COLUNAS:
        raise ValidationError(f"No máximo {MAX_COLUNAS} colunas.")
    campo_data = dados.get("campo_data") or ""
    if campo_data and (campo_data not in por_nome or por_nome[campo_data].tipo != "D"):
        raise ValidationError("Campo de data inválido.")
    busca = dados.get("campo_busca") or ""
    if busca and (busca not in por_nome or por_nome[busca].tipo != "C"):
        raise ValidationError("O campo de busca por nome precisa ser uma das colunas escolhidas, do tipo texto.")
    filtraveis = [c for c in dict.fromkeys(lista("filtraveis")) if c]
    invalidos = [c for c in filtraveis if c not in colunas or por_nome[c].tipo != "C"]
    if invalidos:
        raise ValidationError(f"Campo filtrável inválido: {', '.join(invalidos)} (precisa ser um campo de texto da tabela).")
    if len(filtraveis) > MAX_FILTRAVEIS:
        raise ValidationError(f"No máximo {MAX_FILTRAVEIS} campos filtráveis.")
    filtros = []
    for n in range(MAX_FILTROS):
        campo, valor = dados.get(f"filtro_campo_{n}") or "", (dados.get(f"filtro_valor_{n}") or "").strip()
        if not campo and not valor:
            continue
        if campo not in por_nome or not CAMPO_RE.match(campo) or not VALOR_FIXO_RE.match(valor):
            raise ValidationError("Filtro fixo inválido (use só letras, números, espaço e hífen).")
        titulo = (por_nome[campo].titulo or "").strip().lower()
        if valor.lower() in (titulo, campo.lower()):
            # Ex.: RA_NOME = "Nome": parece engano (quis escolher a coluna), e zeraria o resultado.
            raise ValidationError(f"O filtro {campo} = “{valor}” parece o nome do campo, não um valor. "
                                  "Deixe o filtro em branco para mostrar o campo como coluna.")
        filtros.append({"campo": campo, "valor": valor})
    setores = list(Department.objects.filter(pk__in=[s for s in lista("setores") if str(s).isdigit()], ativo=True))
    if not setores:
        raise ValidationError("Escolha ao menos um setor que poderá usar a consulta.")

    spec = {
        "colunas": [{"campo": c, "rotulo": por_nome[c].titulo or c, "tipo": _tipo(por_nome[c])} for c in colunas],
        "campo_data": campo_data,
        "campo_busca": busca,  # vazio: escolhido automaticamente (contratos.campo_busca)
        "filtraveis": filtraveis,  # vazio: todas as colunas de texto (contratos.campos_filtraveis)
        "filtros_fixos": filtros,
        "ocultos": sorted(set(por_nome) - set(colunas)),
    }
    base = slugify(nome)[:40] or "consulta"
    codigo, n = f"{PREFIXO_APRENDIDO}{base}", 2
    while ConsultaModelo.objects.filter(codigo=codigo).exists():
        codigo, n = f"{PREFIXO_APRENDIDO}{base}-{n}", n + 1
    cm = ConsultaModelo(codigo=codigo, nome=nome, descricao=(dados.get("descricao") or "")[:300],
                        tabela=info["alias"], spec=spec, criado_por=user)
    cm.full_clean()
    _testar_compilacao(cm)
    cm.save()
    cm.setores.set(setores)
    registrar("parametro_registrado", "agente", usuario=user, entidade_id=cm.pk, estado_novo="modelo_proposto")
    return cm


def _testar_compilacao(cm, camada=None):
    """Compila e valida com valores de exemplo, sem executar nada (com a camada da pessoa, se houver)."""
    from iativa.query.compilador import compilar
    from iativa.query.plano import FILTROS_USUARIO, montar_modelo
    from iativa.query.validador import validar

    valores = {"filial-analise": "01", "periodo-analise": {"inicio": "2026-01-01", "fim": "2026-01-31"},
               FILTROS_USUARIO: camada or {}}
    plano = montar_modelo(cm.tabela, cm.spec, valores, cm.codigo)
    with abrir_catalogo() as cat:
        comp = compilar(plano, cat, 10)
        aliases = [cm.tabela] + [j["tabela"] for j in cm.spec.get("juncoes", []) + (camada or {}).get("juncoes", [])]
        fisicas = {t.fisica for t in cat.tabelas(aliases).values()}
    validar(comp.sql, fisicas, 11)


def _autoaprovacao() -> bool:
    return bool(getattr(settings, "IATIVA_APRENDIZADO_AUTOAPROVACAO", False))


def pode_aprovar(user, cm) -> bool:
    if cm.situacao != SituacaoModelo.PROPOSTA:
        return False
    from iativa.accounts.authz import permissao_geral

    if permissao_geral(user):
        return True  # M40: permissão geral (TI) aprova na hora, inclusive a própria consulta
    if cm.criado_por_id == user.pk and not _autoaprovacao():
        return False
    if tem_permissao(user, Permissao.APROVAR_CONHECIMENTO):
        return True
    return any(tem_permissao(user, Permissao.APROVAR_CONHECIMENTO, s) for s in cm.setores.all())


@transaction.atomic
def aprovar(user, cm) -> ConsultaModelo:
    cm = ConsultaModelo.objects.select_for_update().get(pk=cm.pk)
    if not pode_aprovar(user, cm):
        raise PermissaoNegada("Sem permissão para aprovar esta consulta (ou foi você quem a propôs).")
    _testar_compilacao(cm)
    cm.situacao, cm.aprovado_por, cm.aprovado_em = SituacaoModelo.APROVADA, user, timezone.now()
    cm.save(update_fields=["situacao", "aprovado_por", "aprovado_em"])
    registrar("parametro_aprovado", "agente", usuario=user, entidade_id=cm.pk, estado_novo="modelo_aprovado")
    return cm


@transaction.atomic
def rejeitar(user, cm) -> ConsultaModelo:
    cm = ConsultaModelo.objects.select_for_update().get(pk=cm.pk)
    if cm.situacao != SituacaoModelo.PROPOSTA or not (
        tem_permissao(user, Permissao.APROVAR_CONHECIMENTO)
        or any(tem_permissao(user, Permissao.APROVAR_CONHECIMENTO, s) for s in cm.setores.all())
    ):
        raise PermissaoNegada("Sem permissão para rejeitar.")
    cm.situacao = SituacaoModelo.REJEITADA
    cm.save(update_fields=["situacao"])
    registrar("parametro_rejeitado", "agente", usuario=user, entidade_id=cm.pk, estado_novo="modelo_rejeitado")
    return cm


MAX_VERSOES = 10


def pode_ajustar(user) -> bool:
    """Quem pode criar consultas (administrador ou aprendizado automático) pode ajustar os filtros delas."""
    return pode_aprender(user) or pode_aprender_sozinho(user)
OPERADORES_AJUSTE = {"igual": "=", "diferente": "<>"}


TODOS, USUARIO = "todos", "usuario"  # M42: camada do T.I (filtros fixos) e camada da pessoa


def alcance_padrao(user) -> str:
    """Quem tem permissão geral (T.I) mexe nos filtros fixos, que valem para todos; os demais criam
    filtros só para si."""
    from iativa.accounts.authz import permissao_geral

    return TODOS if permissao_geral(user) else USUARIO


def camada_por_codigo(user, codigo: str) -> dict:
    """Camada da pessoa para executar a consulta aprendida `codigo` (vazia se não houver)."""
    from .models import FiltroUsuario

    fu = FiltroUsuario.objects.filter(usuario=user, consulta__codigo=codigo).first() if user is not None else None
    return {k: v for k, v in fu.camada.items() if k != "anteriores"} if fu is not None else {}


def camada_usuario(user, cm) -> dict:
    from .models import FiltroUsuario

    fu = FiltroUsuario.objects.filter(usuario=user, consulta=cm).first() if user is not None else None
    return dict(fu.camada) if fu is not None else {}


@transaction.atomic
def ajustar_filtros(user, cm, filtros: list[dict], remover: list[str], alcance: str = "",
                    criacao: bool = False) -> ConsultaModelo:
    """M40/M42: muda os filtros de uma consulta aprendida a pedido da pessoa. Inclui/troca (`filtros`:
    campo, operador igual|diferente, valor) e tira (`remover`) filtros; o campo pode ser de uma tabela
    ligada pelo dicionário (junção pela chave única, sem duplicar linhas). A versão anterior fica guardada
    para `voltar_filtros`. Alcance "todos" (filtros fixos, só T.I) ou "usuario" (só para quem pediu; pode
    também desligar, para si, um filtro fixo). A consulta compilada é validada antes."""
    # criacao: a consulta que a própria pessoa acabou de criar nasce com os filtros da definição (fixos).
    alcance = TODOS if criacao else (alcance or alcance_padrao(user))
    if alcance == USUARIO:
        return _ajustar_do_usuario(user, cm, filtros, remover)
    if not pode_ajustar(user) or (alcance_padrao(user) != TODOS and not criacao):
        raise PermissaoNegada("Só o T.I (permissão geral) muda o filtro que vale para todos. "
                              "Posso criar esse filtro só para você.")
    cm = ConsultaModelo.objects.select_for_update().get(pk=cm.pk)
    spec = dict(cm.spec)
    juncoes = [dict(j) for j in spec.get("juncoes", [])]
    atuais = {f["campo"]: dict(f) for f in spec.get("filtros_fixos", [])}
    for campo in remover:
        if campo not in atuais:
            raise ValidationError(f"A consulta não tem filtro em {campo}.")
        atuais.pop(campo)
    _novos_filtros(user, cm, atuais, juncoes, filtros, "j")
    anteriores = list(spec.get("anteriores", []))
    anteriores.append({"filtros_fixos": spec.get("filtros_fixos", []), "juncoes": spec.get("juncoes", [])})
    spec.update(filtros_fixos=list(atuais.values()), juncoes=juncoes, anteriores=anteriores[-MAX_VERSOES:])
    cm.spec = spec
    _testar_compilacao(cm)
    cm.save(update_fields=["spec"])
    registrar("parametro_corrigido", "agente", usuario=user, entidade_id=cm.pk, estado_novo="filtros_ajustados")
    return cm


def _ajustar_do_usuario(user, cm, filtros, remover) -> ConsultaModelo:
    from .models import FiltroUsuario

    fu, _ = FiltroUsuario.objects.select_for_update().get_or_create(usuario=user, consulta=cm)
    camada = dict(fu.camada)
    atuais = {f["campo"]: dict(f) for f in camada.get("filtros", [])}
    juncoes = [dict(j) for j in camada.get("juncoes", [])]
    desligados = list(camada.get("desligados", []))
    fixos = {f["campo"] for f in cm.spec.get("filtros_fixos", [])}
    for campo in remover:
        if campo in atuais:
            atuais.pop(campo)
        elif campo in fixos and campo not in desligados:
            desligados.append(campo)  # o filtro do T.I deixa de valer só para esta pessoa
        else:
            raise ValidationError(f"A consulta não tem filtro em {campo}.")
    _novos_filtros(user, cm, atuais, juncoes, filtros, "u", cm.spec.get("juncoes", []))
    for campo in atuais:
        if campo in fixos and campo not in desligados:
            desligados.append(campo)  # o critério da pessoa substitui o do T.I no mesmo campo
    usados = {f.get("alias") for f in atuais.values()}
    juncoes = [j for j in juncoes if j["alias"] in usados]
    anteriores = list(camada.get("anteriores", []))
    anteriores.append({k: camada.get(k, []) for k in ("filtros", "juncoes", "desligados")})
    nova = {"filtros": list(atuais.values()), "juncoes": juncoes, "desligados": desligados,
            "anteriores": anteriores[-MAX_VERSOES:]}
    _testar_compilacao(cm, nova)
    fu.camada = nova
    fu.save()
    registrar("parametro_corrigido", "agente", usuario=user, entidade_id=cm.pk, estado_novo="filtros_do_usuario")
    return cm


def _novos_filtros(user, cm, atuais: dict, juncoes: list, filtros: list[dict], prefixo: str,
                   juncoes_base=()) -> None:
    """Valida e aplica `filtros` em `atuais`/`juncoes`. Campo de tabela ligada pelo dicionário entra com a
    junção pela chave; uma tabela já ligada em `juncoes_base` (camada do T.I) é reaproveitada."""
    por_nome = {c.nome: c for c in campos_da_tabela(user, cm.tabela, automatico=not pode_aprender(user))["campos"]}
    possiveis = None
    for f in filtros:
        campo, valor, op = f["campo"], f["valor"].strip(), OPERADORES_AJUSTE.get(f["operador"])
        if op is None or not CAMPO_RE.match(campo) or not VALOR_FIXO_RE.match(valor):
            raise ValidationError(f"Filtro inválido em {campo} (valor só com letras, números, espaço e hífen).")
        if campo in por_nome:
            if sensivel(por_nome[campo]):
                raise PermissaoNegada("Campo indisponível para filtro.")
            atuais[campo] = {"campo": campo, "op": op, "valor": valor}
            continue
        if possiveis is None:
            possiveis = juncoes_possiveis(user, cm.tabela)
        j = next((j for j in possiveis if any(c.nome == campo for c in j["campos"])), None)
        if j is None:
            raise ValidationError(f"O campo {campo} não está em {cm.tabela} nem numa tabela ligada a ela pelo dicionário.")
        existente = next((x for x in list(juncoes_base) + juncoes if x["tabela"] == j["tabela"]), None)
        if existente is None:
            if len(juncoes) + len(juncoes_base) >= MAX_JUNCOES + 1:
                raise ValidationError("A consulta já tem o máximo de tabelas ligadas.")
            usados = {x["alias"] for x in juncoes}
            apelido = next(f"{prefixo}{n}" for n in range(1, 10) if f"{prefixo}{n}" not in usados)
            existente = {"tabela": j["tabela"], "alias": apelido, "pares": j["pares"]}
            juncoes.append(existente)
        atuais[campo] = {"campo": campo, "op": op, "valor": valor, "alias": existente["alias"]}


@transaction.atomic
def voltar_filtros(user, cm, alcance: str = "") -> ConsultaModelo:
    """M40/M42: desfaz o último ajuste da camada (filtros fixos do T.I ou filtros da própria pessoa)."""
    alcance = alcance or alcance_padrao(user)
    if alcance == USUARIO:
        from .models import FiltroUsuario

        fu = FiltroUsuario.objects.select_for_update().filter(usuario=user, consulta=cm).first()
        anteriores = list((fu.camada if fu else {}).get("anteriores", []))
        if not anteriores:
            raise ValidationError("Você não tem ajuste de filtro nessa consulta para desfazer.")
        fu.camada = {**anteriores.pop(), "anteriores": anteriores}
        fu.save()
        registrar("parametro_corrigido", "agente", usuario=user, entidade_id=cm.pk,
                  estado_novo="filtros_do_usuario_revertidos")
        return cm
    if not pode_ajustar(user) or alcance_padrao(user) != TODOS:
        raise PermissaoNegada("Só o T.I (permissão geral) muda o filtro que vale para todos.")
    cm = ConsultaModelo.objects.select_for_update().get(pk=cm.pk)
    spec = dict(cm.spec)
    anteriores = list(spec.get("anteriores", []))
    if not anteriores:
        raise ValidationError("Essa consulta não tem ajuste de filtro para desfazer.")
    v = anteriores.pop()
    spec.update(filtros_fixos=v["filtros_fixos"], juncoes=v["juncoes"], anteriores=anteriores)
    cm.spec = spec
    _testar_compilacao(cm)
    cm.save(update_fields=["spec"])
    registrar("parametro_corrigido", "agente", usuario=user, entidade_id=cm.pk, estado_novo="filtros_revertidos")
    return cm


def _texto_filtros(fs) -> str:
    return "; ".join(f"{f['campo']} {'diferente de' if f.get('op') == '<>' else '='} “{f['valor']}”" for f in fs)


def descrever_filtros(cm, user=None) -> str:
    """Filtros fixos (T.I) e, se houver, os da pessoa."""
    camada = camada_usuario(user, cm)
    desligados = set(camada.get("desligados", []))
    fixos = [f for f in cm.spec.get("filtros_fixos", []) if f["campo"] not in desligados]
    if not camada:
        return _texto_filtros(fixos) if fixos else "sem filtros fixos"
    partes = [("fixos: " + _texto_filtros(fixos)) if fixos else "sem filtros fixos"]
    if camada.get("filtros"):
        partes.append("só seus: " + _texto_filtros(camada["filtros"]))
    if desligados:
        partes.append("fixos que não valem para você: " + ", ".join(sorted(desligados)))
    return " · ".join(partes)
