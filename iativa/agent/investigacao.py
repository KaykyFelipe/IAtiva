"""Investigação de vínculos de uma lista inteira (M25).

Uso típico: a pessoa vê uma lista (ex.: funcionários desligados) e pergunta se "todos foram
demitidos mesmo", dando um caso como exemplo. O exemplo é pista, não escopo: aqui se investiga
TODOS os integrantes da lista original.

Divisão de trabalho:
- o modelo escolhe, entre os campos do dicionário, quais representam início, fim, situação,
  cargo e categoria do vínculo, e se a identidade é pelo documento ou pelo nome;
- o backend valida essa escolha no dicionário, reexecuta a lista com os critérios originais,
  busca os outros registros em lote, compara datas e campos de forma determinística, classifica
  cada pessoa e confere a cobertura.

Nada aqui é gravado: itens, documentos e evidências vivem só no `Contexto`, em memória.
O documento (CPF) só é usado se `IATIVA_VINCULO_POR_DOCUMENTO` estiver ligado, apenas para
comparar, nunca exibido, e trocado por um resumo criptográfico logo depois da busca.
"""
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass, field
from datetime import date

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

from iativa.accounts.authz import Permissao, PermissaoNegada, filiais_autorizadas, tem_permissao
from iativa.accounts.filiais import TODAS, rotulo

from .aprendizado import sensivel

PAPEIS = ("inicio", "fim", "situacao", "cargo", "categoria")
OBRIGATORIOS = ("inicio", "fim")
ROTULO_PAPEL = {"inicio": "início do vínculo", "fim": "fim do vínculo (desligamento)", "situacao": "situação",
                "cargo": "cargo/função", "categoria": "categoria"}
DOCUMENTO_RE = re.compile(r"CPF|_CIC\b", re.I)


class InvestigacaoInvalida(ValueError):
    """Plano que não confere com o dicionário: corrigível escolhendo outros campos."""


class SemPermissao(RuntimeError):
    pass


class Cancelada(RuntimeError):
    pass


class LimiteAtingido(RuntimeError):
    pass


class Transitoria(RuntimeError):
    """Falha passageira (rede, dicionário fora do ar): a etapa pode ser repetida."""


# --- plano --------------------------------------------------------------------------------------

def _base_objetivo(base):
    from .contratos import OBJETIVOS, objetivos_para

    if base is None:
        raise InvestigacaoInvalida("Não há uma lista consultada nesta conversa para investigar.")
    if base.objetivo not in OBJETIVOS:
        raise InvestigacaoInvalida("A consulta que gerou a lista não existe mais.")
    obj = OBJETIVOS[base.objetivo]
    if not obj.modelo_id:
        raise InvestigacaoInvalida("A investigação de vínculos usa consultas aprendidas; a lista veio de uma consulta "
                                   "fixa do Financeiro.")
    if base.objetivo not in {o.codigo for o in objetivos_para(base.usuario)}:
        raise SemPermissao("A consulta que gerou a lista não está mais liberada para você.")
    return obj


def conferir_permissao(user, base):
    """Reconfere, a cada etapa, que a pessoa ainda pode consultar o setor e a consulta da lista."""
    if base.usuario_id != user.pk:
        raise SemPermissao("Só quem fez a lista pode investigá-la.")
    if not tem_permissao(user, Permissao.CONSULTAR, base.setor):
        raise SemPermissao("Seu usuário perdeu a permissão de consulta neste setor.")
    if not filiais_autorizadas(user, base.setor):
        raise SemPermissao("Seu usuário não tem mais filiais autorizadas neste setor.")
    from . import orquestrador

    obj = _base_objetivo(base)
    motivo = orquestrador._verificar_tabela_aprendida(user, obj)
    if motivo == "metadados_indisponiveis":
        raise Transitoria("O dicionário do ERP não respondeu agora.")
    if motivo:
        raise SemPermissao(orquestrador.MOTIVOS.get(motivo, "Sem acesso à tabela da lista."))
    return obj


def _acesso(user, base, filial):
    """Conferência leve antes de cada consulta (permissão e filial podem mudar no meio da tarefa)."""
    if not tem_permissao(user, Permissao.CONSULTAR, base.setor):
        raise SemPermissao("Seu usuário perdeu a permissão de consulta neste setor durante a verificação.")
    if filial not in filiais_autorizadas(user, base.setor):
        raise SemPermissao(f"A filial {rotulo(filial)} deixou de estar autorizada durante a verificação.")


def validar_plano(user, base, proposta: dict) -> dict:
    """Confere a escolha do modelo no dicionário. Devolve só metadados (nomes de campos): é o que
    pode ser gravado no plano da tarefa."""
    from iativa.metadata.catalogo import abrir_catalogo

    from .contratos import campo_busca
    from .models import ConsultaModelo

    obj = conferir_permissao(user, base)
    tabela = obj.tabelas[0]
    proposta = proposta if isinstance(proposta, dict) else {}
    try:
        with abrir_catalogo() as cat:
            t = cat.tabelas([tabela], com_campos=True).get(tabela)
    except Exception:
        raise Transitoria("O dicionário do ERP não respondeu agora.") from None
    if t is None:
        raise InvestigacaoInvalida(f"A tabela {tabela} não está no dicionário.")
    campos = {c.nome: c for c in t.campos if c.consultavel}
    chave = [c for c in (t.chave or ()) if c != t.campo_filial and not c.endswith("_FILIAL")]
    if not chave or any(c not in campos for c in chave):
        raise InvestigacaoInvalida(f"A tabela {tabela} não tem chave única utilizável no dicionário.")
    papeis = {}
    escolhidos = proposta.get("campos") if isinstance(proposta.get("campos"), dict) else {}
    for papel in PAPEIS:
        nome = str(escolhidos.get(papel, "") or "").strip().upper()
        if not nome:
            if papel in OBRIGATORIOS:
                raise InvestigacaoInvalida(f"Falta o campo de {ROTULO_PAPEL[papel]} da tabela {tabela}.")
            continue
        c = campos.get(nome)
        if c is None or nome == t.campo_filial or sensivel(c):
            raise InvestigacaoInvalida(f"O campo {nome} ({ROTULO_PAPEL[papel]}) não existe ou não pode ser "
                                       f"consultado na tabela {tabela}.")
        if papel in ("inicio", "fim") and c.tipo != "D":
            raise InvestigacaoInvalida(f"O campo {nome} não é de data; não serve para {ROTULO_PAPEL[papel]}.")
        papeis[papel] = nome
    if papeis["inicio"] == papeis["fim"]:
        raise InvestigacaoInvalida("Início e fim do vínculo precisam ser campos diferentes.")
    cm = ConsultaModelo.objects.get(pk=obj.modelo_id)
    nome = campo_busca(cm.spec)
    if not nome:
        nome = next((n for n, c in campos.items() if re.search(r"_NOME$", n) and not sensivel(c)), "")
    identidade = "documento" if proposta.get("identidade") == "documento" else "nome"
    documento, aviso = "", ""
    if identidade == "documento":
        documento = next((n for n, c in campos.items()
                          if DOCUMENTO_RE.search(f"{n} {c.titulo or ''}")), "")
        if not getattr(settings, "IATIVA_VINCULO_POR_DOCUMENTO", False):
            identidade, documento = "nome", ""
            aviso = ("Comparação pelo documento não está autorizada (IATIVA_VINCULO_POR_DOCUMENTO); usei o nome, "
                     "que não confirma identidade.")
        elif not documento:
            identidade = "nome"
            aviso = "A tabela não tem campo de documento no dicionário; usei o nome, que não confirma identidade."
    if identidade == "nome" and not nome:
        raise InvestigacaoInvalida("Sem campo de nome nem de documento para achar outros vínculos da mesma pessoa.")
    return {"tabela": tabela, "chave": chave, "nome": nome, "papeis": papeis, "identidade": identidade,
            "documento": documento, "aviso": aviso,
            "titulos": {n: (campos[n].titulo or n) for n in chave + list(papeis.values()) + ([nome] if nome else [])}}


# --- execução -----------------------------------------------------------------------------------

@dataclass
class Registro:
    filial: str
    chave: tuple
    nome: str
    valores: dict  # papel -> texto


@dataclass
class Item:
    reg: Registro
    ident: str = ""  # resumo do documento (HMAC) ou nome normalizado
    classe: str = ""
    verificado: bool | None = None
    texto: str = ""
    evidencias: list = field(default_factory=list)
    motivo: str = ""


@dataclass
class Contexto:
    """Evidências transitórias da execução. Some ao terminar ou se o processo reiniciar."""
    itens: list = field(default_factory=list)
    lista_truncada: bool = False
    relacionados: dict = field(default_factory=dict)  # ident -> [Registro]
    lotes: int = 0
    lotes_feitos: int = 0
    ultima_data: str = ""
    _sal: bytes = field(default_factory=lambda: secrets.token_bytes(16))
    _documentos: dict = field(default_factory=dict)  # ident -> documento (só até a busca em lote)


class Controle:
    """Checa limites antes de cada consulta. A tarefa sobrescreve `checar` (cancelamento, posse)."""

    def __init__(self, limites: dict):
        self.limites = limites
        self.consultas = 0

    def checar(self):
        from . import prazo

        if prazo.esgotado():
            raise LimiteAtingido("O prazo total da tarefa acabou.")

    def antes_de_consultar(self):
        self.checar()
        if self.consultas >= int(self.limites.get("consultas", 100)):
            raise LimiteAtingido(f"Limite de {self.limites.get('consultas')} consultas atingido.")
        self.consultas += 1

    def progresso(self, texto: str):
        pass


def _norm(t) -> str:
    return " ".join(str(t or "").upper().split())


def _data(v) -> str:
    t = str(v or "").strip()
    return t if re.fullmatch(r"\d{8}", t) else ""


def _fmt(d: str) -> str:
    return f"{d[6:8]}/{d[4:6]}/{d[0:4]}" if d else "sem data"


def _consultar(cat, plano, ctl, limite):
    from iativa.query.compilador import compilar
    from iativa.query.executor import executar
    from iativa.query.servico import _cfg
    from iativa.query.validador import validar

    ctl.antes_de_consultar()
    comp = compilar(plano, cat, limite)
    permitidas = {t.fisica for t in cat.tabelas([plano.base]).values()}
    validar(comp.sql, permitidas, limite + 1)
    linhas, cortou = executar(cat, comp.sql, comp.params, limite, _cfg()["timeout_s"],
                              validar_final=lambda sql: validar(sql, permitidas, limite + 1))
    return comp, linhas, cortou


def _colunas(plano_inv):
    from iativa.query.plano import Campo, Coluna

    campos = list(plano_inv["chave"]) + ([plano_inv["nome"]] if plano_inv["nome"] else []) + list(
        plano_inv["papeis"].values()) + ([plano_inv["documento"]] if plano_inv["documento"] else [])
    return [Coluna(c, Campo("e1", c)) for c in dict.fromkeys(campos)]


def _registro(plano_inv, filial, linha, rotulos) -> tuple[Registro, str]:
    v = dict(zip(rotulos, linha))
    papeis = {p: str(v.get(c) or "").strip() for p, c in plano_inv["papeis"].items()}
    reg = Registro(filial, tuple(str(v[c]).strip() for c in plano_inv["chave"]),
                   _norm(v.get(plano_inv["nome"])) if plano_inv["nome"] else "", papeis)
    doc = str(v.get(plano_inv["documento"]) or "").strip() if plano_inv["documento"] else ""
    return reg, doc


def _ident(ctx, plano_inv, reg, doc) -> str:
    if plano_inv["identidade"] == "documento":
        if not doc:
            return ""
        h = hmac.new(ctx._sal, doc.encode(), hashlib.sha256).hexdigest()
        ctx._documentos.setdefault(h, doc)
        return h
    return reg.nome


def _ver_datas(ctx, reg):
    for p in ("inicio", "fim"):
        d = _data(reg.valores.get(p))
        if d and d > ctx.ultima_data:
            ctx.ultima_data = d


def reconstruir_lista(user, base, plano_inv, ctx: Contexto, ctl: Controle):
    """Reexecuta a lista original com os mesmos critérios (filial, período, filtros, regras)."""
    from iativa.metadata.catalogo import abrir_catalogo
    from iativa.query import servico
    from iativa.query.plano import REGRAS, montar

    from . import memoria

    try:
        valores = servico._valores(user, base)
    except Exception:
        raise InvestigacaoInvalida("Os critérios da lista original não valem mais (algum parâmetro mudou).") from None
    valores.pop("agregacao", None)  # a investigação parte da lista, não do cálculo
    autorizadas = filiais_autorizadas(user, base.setor)
    filiais = sorted(autorizadas) if valores.get("filial-analise") == TODAS else [valores.get("filial-analise")]
    if not filiais or any(f not in autorizadas for f in filiais):
        raise SemPermissao("A filial da lista original não está mais entre as suas filiais autorizadas.")
    valores[REGRAS] = memoria.regras_aplicaveis(user, base.setor, plano_inv["tabela"])
    limite = int(ctl.limites.get("registros", 2000))
    ctx.itens, ctx.lista_truncada = [], False
    vistos = set()
    with abrir_catalogo() as cat:
        for f in filiais:
            ctl.progresso(f"Reexecutando a lista original ({rotulo(f)}).")
            _acesso(user, base, f)
            p = montar(base.objetivo, {**valores, "filial-analise": f})
            p.colunas, p.agrupar, p.top = _colunas(plano_inv), False, 0
            p.ordenar = [(plano_inv["chave"][0], False)]
            comp, linhas, cortou = _consultar(cat, p, ctl, max(1, limite - len(ctx.itens)))
            ctx.lista_truncada = ctx.lista_truncada or cortou
            for linha in linhas:
                reg, doc = _registro(plano_inv, f, linha, comp.rotulos)
                if (reg.filial, reg.chave) in vistos:
                    continue
                vistos.add((reg.filial, reg.chave))
                _ver_datas(ctx, reg)
                ctx.itens.append(Item(reg, _ident(ctx, plano_inv, reg, doc)))
            if len(ctx.itens) >= limite:
                ctx.lista_truncada = ctx.lista_truncada or f != filiais[-1]
                break


def _literal_ok(valor: str, tamanho: int) -> bool:
    from iativa.query.executor import LITERAL_SEGURO

    v = valor.ljust(tamanho or len(valor))
    return bool(LITERAL_SEGURO.match(v)) and len(v) <= 40


def buscar_relacionados(user, base, plano_inv, ctx: Contexto, ctl: Controle):
    """Outros registros da mesma pessoa em TODAS as filiais autorizadas, em lotes (IN), sem uma
    consulta por pessoa. Lote cortado pelo limite deixa os seus itens inconclusivos, nunca calados."""
    from iativa.metadata.catalogo import abrir_catalogo
    from iativa.query.plano import Campo, Filtro, Plano

    campo = plano_inv["documento"] if plano_inv["identidade"] == "documento" else plano_inv["nome"]
    ctx.relacionados = {}
    lote = max(1, int(ctl.limites.get("lote", 40)))
    limite = int(ctl.limites.get("registros", 2000))
    with abrir_catalogo() as cat:
        t = cat.tabelas([plano_inv["tabela"]], com_campos=True)[plano_inv["tabela"]]
        tamanho = next((c.tamanho for c in t.campos if c.nome == campo), 0) or 0
        valores = {}
        for it in ctx.itens:
            if not it.ident:
                it.verificado, it.classe = False, "inconclusivo"
                it.motivo = "o registro não tem o campo usado para comparar preenchido"
                continue
            bruto = ctx._documentos.get(it.ident) if plano_inv["identidade"] == "documento" else it.reg.nome
            if not _literal_ok(bruto, tamanho):
                it.verificado, it.classe = False, "inconclusivo"
                it.motivo = "o valor usado para comparar tem caracteres que a busca em lote não aceita"
                continue
            valores[it.ident] = bruto
        idents = list(valores)
        lotes = [idents[i:i + lote] for i in range(0, len(idents), lote)]
        filiais = sorted(filiais_autorizadas(user, base.setor))
        if not filiais or not tem_permissao(user, Permissao.CONSULTAR, base.setor):
            raise SemPermissao("Seu usuário perdeu a permissão de consulta neste setor durante a verificação.")
        ctx.lotes, ctx.lotes_feitos = len(lotes) * len(filiais), 0
        cortados = set()
        for f in filiais:
            for parte in lotes:
                ctl.progresso(f"Buscando outros vínculos em lote ({ctx.lotes_feitos + 1} de {ctx.lotes}).")
                _acesso(user, base, f)
                p = Plano(objetivo="investigacao", base=plano_inv["tabela"], filial=f)
                p.filtros = [Filtro(Campo("e1", t.campo_filial), "=", f),
                             Filtro(Campo("e1", campo), "IN", tuple(valores[i] for i in parte))]
                p.colunas = _colunas(plano_inv)
                comp, linhas, cortou = _consultar(cat, p, ctl, limite)
                if cortou:
                    cortados |= set(parte)
                for linha in linhas:
                    reg, doc = _registro(plano_inv, f, linha, comp.rotulos)
                    _ver_datas(ctx, reg)
                    ident = _ident(ctx, plano_inv, reg, doc)
                    ctx.relacionados.setdefault(ident, []).append(reg)
                ctx.lotes_feitos += 1
    ctx._documentos.clear()  # o documento não sai daqui: sobram só os resumos
    for it in ctx.itens:
        if it.ident in cortados and it.verificado is None:
            it.verificado, it.classe = False, "inconclusivo"
            it.motivo = "a busca em lote passou do limite de registros; os vínculos dessa pessoa ficaram incompletos"


def _desc(plano_inv, reg: Registro) -> str:
    t = plano_inv["titulos"]
    partes = [f"filial {rotulo(reg.filial)}, {t.get(plano_inv['chave'][-1], 'chave')} {'/'.join(reg.chave)}",
              f"início {_fmt(_data(reg.valores.get('inicio')))}",
              f"fim {_fmt(_data(reg.valores.get('fim')))}" if _data(reg.valores.get("fim")) else "sem data de fim"]
    for p in ("situacao", "cargo", "categoria"):
        if p in plano_inv["papeis"]:
            partes.append(f"{t.get(plano_inv['papeis'][p], p)}: {reg.valores.get(p) or 'em branco'}")
    return "; ".join(partes)


def classificar(plano_inv, ctx: Contexto):
    """Compara datas e campos de cada pessoa com os outros registros. Determinístico; não presume
    motivo (efetivação, promoção) nem trata nome igual como mesma pessoa."""
    por_nome = plano_inv["identidade"] == "nome"
    for it in ctx.itens:
        r = it.reg
        it.evidencias = ["Registro da lista: " + _desc(plano_inv, r)]
        if it.verificado is False:
            it.texto = "Inconclusivo: " + it.motivo + "."
            continue
        ini, fim = _data(r.valores.get("inicio")), _data(r.valores.get("fim"))
        outros = [o for o in ctx.relacionados.get(it.ident, []) if (o.filial, o.chave) != (r.filial, r.chave)]
        it.evidencias += [("Mesmo nome: " if por_nome else "Mesmo documento: ") + _desc(plano_inv, o) for o in outros]
        if not fim:
            it.classe, it.verificado = "sem_fim", True
            it.texto = "O registro da lista não tem data de fim: pela base, este vínculo não está encerrado."
            continue
        if not outros:
            it.classe, it.verificado = "encerrado_sem_outro", True
            it.texto = (f"Vínculo encerrado em {_fmt(fim)}. Nenhum outro registro com o mesmo "
                        f"{'nome' if por_nome else 'documento'} nas filiais autorizadas.")
            if por_nome:
                it.texto += " A busca foi pelo nome: um novo vínculo com o nome escrito de outro jeito não aparece."
            continue
        posteriores = sorted((o for o in outros if _data(o.valores.get("inicio")) >= fim), key=lambda o: _data(
            o.valores.get("inicio")))
        simultaneos = [o for o in outros if o not in posteriores and _data(o.valores.get("inicio")) < fim
                       and (not _data(o.valores.get("fim")) or _data(o.valores.get("fim")) > ini)]
        if por_nome:
            it.classe, it.verificado = "mesmo_nome", False
            alvo = posteriores[0] if posteriores else (simultaneos[0] if simultaneos else outros[0])
            it.texto = (f"Inconclusivo: vínculo encerrado em {_fmt(fim)} e há {len(outros)} outro(s) registro(s) com o "
                        f"mesmo nome (ex.: início em {_fmt(_data(alvo.valores.get('inicio')))}). Sem um documento não "
                        "dá para afirmar que é a mesma pessoa.")
            if len(outros) > 1:
                it.texto += " Há mais de um candidato."
            continue
        if posteriores:
            o = posteriores[0]
            oi, of = _data(o.valores.get("inicio")), _data(o.valores.get("fim"))
            it.classe, it.verificado = "novo_vinculo_posterior", True
            it.texto = (f"Vínculo encerrado em {_fmt(fim)}; a mesma pessoa (mesmo documento) tem novo vínculo a partir de "
                        f"{_fmt(oi)}, " + ("sem data de fim (ativo pela base)" if not of else f"encerrado em {_fmt(of)}")
                        + ".")
            if o.filial != r.filial and (date(int(oi[:4]), int(oi[4:6]), int(oi[6:])) -
                                         date(int(fim[:4]), int(fim[4:6]), int(fim[6:]))).days <= 1:
                it.texto += " Começou em outra filial logo após o encerramento: pode ser transferência (não confirmado)."
            for p in ("cargo", "categoria"):
                if p in plano_inv["papeis"] and (r.valores.get(p) or "") != (o.valores.get(p) or ""):
                    it.texto += (f" {ROTULO_PAPEL[p].capitalize()} diferente: {r.valores.get(p) or 'em branco'} → "
                                 f"{o.valores.get(p) or 'em branco'} (os dados não dizem o motivo).")
            it.texto += " Um novo vínculo não comprova, sozinho, efetivação ou promoção."
        elif simultaneos:
            it.classe, it.verificado = "vinculo_simultaneo", True
            it.texto = (f"Vínculo encerrado em {_fmt(fim)}; a mesma pessoa tem outro vínculo no mesmo período, que "
                        "começou antes do encerramento.")
        else:
            it.classe, it.verificado = "so_vinculo_anterior", True
            it.texto = f"Vínculo encerrado em {_fmt(fim)}; só há vínculo anterior da mesma pessoa, já encerrado."


ROTULO_CLASSE = {
    "encerrado_sem_outro": "Encerrado, sem outro vínculo encontrado",
    "novo_vinculo_posterior": "Encerrado, com novo vínculo depois",
    "vinculo_simultaneo": "Encerrado, com outro vínculo no mesmo período",
    "so_vinculo_anterior": "Encerrado, só com vínculo anterior",
    "sem_fim": "Sem data de fim (não encerrado)",
    "mesmo_nome": "Inconclusivo: mesmo nome em outro registro",
    "inconclusivo": "Inconclusivo",
}


def cobertura(ctx: Contexto) -> dict:
    """Conferência do backend: cada item da lista tem classificação ou motivo de inconclusão."""
    sem = [it for it in ctx.itens if it.verificado is None or not it.texto]
    return {"total": len(ctx.itens), "verificados": sum(1 for it in ctx.itens if it.verificado),
            "inconclusivos": sum(1 for it in ctx.itens if it.verificado is False), "sem_classificacao": len(sem),
            "lista_completa": not ctx.lista_truncada}


@dataclass
class Conclusao:
    itens: list
    cobertura: dict
    resumo: list
    referencia: str
    limitacoes: list
    consultado_em: str
    titulos: dict


def concluir(plano_inv, ctx: Contexto, extra_limitacoes=()) -> Conclusao:
    cob = cobertura(ctx)
    resumo = {}
    for it in ctx.itens:
        resumo[ROTULO_CLASSE.get(it.classe, it.classe)] = resumo.get(ROTULO_CLASSE.get(it.classe, it.classe), 0) + 1
    limitacoes = list(extra_limitacoes)
    if plano_inv.get("aviso"):
        limitacoes.append(plano_inv["aviso"])
    if ctx.lista_truncada:
        limitacoes.append("A lista original passou do limite de registros: só os primeiros foram verificados.")
    limitacoes.append("Lista e vínculos consultados de novo agora; se a base mudou desde a lista original, o "
                      "conjunto pode ser diferente.")
    hoje = timezone.localdate()
    ref = f"Data de referência: {hoje:%d/%m/%Y}." + (
        f" Data mais recente vista nesses registros: {_fmt(ctx.ultima_data)} (a base pode estar atrasada em relação "
        "a hoje)." if ctx.ultima_data else "")
    return Conclusao(ctx.itens, cob, sorted(resumo.items(), key=lambda x: -x[1]), ref, limitacoes,
                     f"{timezone.localtime():%d/%m/%Y %H:%M}", plano_inv["titulos"])


def investigar(user, base, plano_inv, ctl: Controle, ctx: Contexto | None = None) -> Conclusao:
    """Execução completa e determinística (sem modelo): usada pelo worker e para mostrar a conclusão."""
    ctx = ctx or Contexto()
    conferir_permissao(user, base)
    reconstruir_lista(user, base, plano_inv, ctx, ctl)
    conferir_permissao(user, base)
    buscar_relacionados(user, base, plano_inv, ctx, ctl)
    classificar(plano_inv, ctx)
    return concluir(plano_inv, ctx)


def contexto_para_modelo(user, base) -> dict | None:
    """Campos do dicionário da tabela da lista (só metadados), para o modelo escolher os papéis."""
    try:
        obj = _base_objetivo(base)
    except (InvestigacaoInvalida, SemPermissao):
        return None
    from . import aprendizado

    try:
        info = aprendizado.campos_da_tabela(user, obj.tabelas[0], automatico=True)
    except (ValidationError, PermissaoNegada, Exception):
        return None
    campos = [{"campo": c.nome, "tipo": {"D": "data", "N": "numero"}.get(c.tipo, "texto"), "titulo": c.titulo}
              for c in info["campos"] if not sensivel(c)][:150]
    tem_doc = any(DOCUMENTO_RE.search(f"{c.nome} {c.titulo or ''}") for c in info["campos"])
    return {"objetivo": obj.codigo, "nome": obj.nome, "tabela": obj.tabelas[0], "campos": campos,
            "documento_disponivel": tem_doc and bool(getattr(settings, "IATIVA_VINCULO_POR_DOCUMENTO", False))}
