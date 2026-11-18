"""Catálogo: junta dicionário (significado) e estrutura física (o que existe de fato).

Regras:
- Campo só é consultável se existir no dicionário E como coluna física, e não for virtual.
- Campo do dicionário sem coluna física fica marcado `sem_coluna_fisica` (inconsistência).
- Coluna física fora do dicionário (exceto as técnicas) fica `sem_dicionario` e não é consultável.
- Personalização vem da marca do dicionário (X3_PROPRI), nunca do prefixo do nome.
"""
from contextlib import contextmanager

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .fontes import DicionarioProtheus, FisicoSqlite, validar_alias
from .tipos import Campo, Relacionamento, Tabela

COLUNAS_TECNICAS = frozenset({"D_E_L_E_T_", "R_E_C_N_O_", "R_E_C_D_E_L_", "S_T_A_M_P_", "I_N_S_D_T_"})
MODOS = {"C": "compartilhado", "E": "exclusivo"}


def nivel_compartilhamento(t) -> str:
    """Do mais específico ao mais amplo: o primeiro nível exclusivo define o que o campo filial guarda."""
    modos = (t.modo, t.modo_unidade or t.modo, t.modo_empresa or t.modo)
    if any(m not in ("C", "E") for m in modos):
        return "desconhecido"
    for nivel, modo in zip(("filial", "unidade", "empresa"), modos):
        if modo == "E":
            return nivel
    return "compartilhado"


class Catalogo:
    def __init__(self, dicionario, fisico, conn=None, dialeto="tsql"):
        self.dicionario = dicionario
        self.fisico = fisico
        self.conn = conn  # mesma conexão somente leitura, usada pela execução (Etapa 5)
        self.dialeto = dialeto

    def vazia(self, fisica: str) -> bool | None:
        """M104: a tabela física não tem nenhum registro nesta instalação? (None = não deu para saber).
        SQL Server: estatística de linhas (sys.partitions), instantânea; sem ler dados."""
        if self.conn is None or not fisica.replace("_", "").isalnum():
            return None
        try:
            cur = self.conn.cursor()
            if self.dialeto == "tsql":
                cur.execute("SELECT SUM(p.rows) FROM sys.partitions p WHERE p.object_id = OBJECT_ID(?) "
                            "AND p.index_id IN (0, 1)", (fisica,))
            else:
                cur.execute(f"SELECT COUNT(*) FROM (SELECT 1 FROM {fisica} LIMIT 1)")
            n = cur.fetchone()[0]
            return None if n is None else int(n) == 0
        except Exception:
            return None

    def tabelas(self, aliases: list[str], com_campos: bool = False) -> dict[str, Tabela]:
        out = {}
        for alias, td in self.dicionario.tabelas([validar_alias(a) for a in aliases]).items():
            colunas = self.fisico.colunas(td.fisica)
            if not colunas:
                continue  # tabela do dicionário sem tabela física: não é oferecida
            filial = f"{alias[1:]}_FILIAL" if alias.startswith("S") else f"{alias}_FILIAL"
            out[alias] = Tabela(
                alias=alias,
                fisica=td.fisica,
                nome=td.nome,
                compartilhamento=nivel_compartilhamento(td),
                exclusao_logica="D_E_L_E_T_" in colunas,
                campo_filial=filial if filial in colunas else None,
                chave=self.dicionario.chave_primaria(alias),
                campos=self._campos(alias, colunas) if com_campos else (),
            )
        return out

    def _campos(self, alias, colunas) -> tuple[Campo, ...]:
        campos, vistos = [], set()
        for cd in self.dicionario.campos(alias):
            vistos.add(cd.nome)
            if cd.virtual:
                situacao = "virtual"
            elif cd.nome not in colunas:
                situacao = "sem_coluna_fisica"
            else:
                situacao = "ok"
            campos.append(Campo(
                nome=cd.nome, tipo=cd.tipo, tamanho=cd.tamanho, decimal=cd.decimal, titulo=cd.titulo,
                descricao=cd.descricao, personalizado=cd.personalizado, consultavel=situacao == "ok",
                situacao=situacao,
            ))
        for nome, col in colunas.items():
            if nome not in vistos and nome not in COLUNAS_TECNICAS:
                campos.append(Campo(
                    nome=nome, tipo=col.tipo, tamanho=None, decimal=None, titulo="", descricao="",
                    personalizado=False, consultavel=False, situacao="sem_dicionario",
                ))
        return tuple(campos)

    def opcoes(self, alias):
        return self.dominios_campos(alias)[0]

    def dominios_campos(self, alias):
        if "X3_CBOX" not in self.fisico.colunas(self.dicionario.sx3):
            opcoes, dinamicos = {}, {}
        else:
            opcoes, dinamicos = self.dicionario.dominios_campos(alias)
        # M106: legenda ensinada pela empresa vale no lugar da rotina dinâmica do Protheus.
        from iativa.agent.legendas import legendas_aprendidas
        try:
            aprendidas = legendas_aprendidas(alias)
        except Exception:  # sem banco da aplicação (ex.: ferramenta isolada): só o dicionário
            aprendidas = {}
        for campo, legenda in aprendidas.items():
            opcoes = {**opcoes, campo: legenda}
            dinamicos = {k: v for k, v in dinamicos.items() if k != campo}
        return opcoes, dinamicos

    def relacionamentos(self, aliases: list[str]) -> list[Relacionamento]:
        return self.dicionario.relacionamentos(aliases)

    def relacionados(self, alias: str) -> list[Relacionamento]:
        return self.dicionario.relacionados(alias)


FONTES = ("sintetico", "protheus")


@contextmanager
def abrir_catalogo():
    """Abre o catálogo da fonte configurada: base sintética ou Protheus real (homologação, liberado
    em 2026-11-10). A fonte real confere a cada abertura que o login continua somente leitura."""
    fonte = settings.IATIVA_METADADOS["fonte"]
    if fonte not in FONTES:
        raise ImproperlyConfigured("Fonte de metadados desconhecida.")
    if fonte == "protheus":
        from . import erp_sqlserver as erp
        from .fontes import InformationSchemaSqlServer

        conn = erp.conectar()
        try:
            if erp.verificar_login(conn):
                raise erp.LoginNaoSomenteLeitura("Login do ERP não é somente leitura: acesso bloqueado.")
            yield Catalogo(DicionarioProtheus(conn, settings.IATIVA_ERP_SUFIXO), InformationSchemaSqlServer(conn),
                           conn=conn, dialeto="tsql")
        finally:
            conn.close()
        return

    from iativa.synthetic_erp.connection import open_readonly
    from iativa.synthetic_erp.dicionario import SUFIXO

    conn = open_readonly()
    try:
        yield Catalogo(DicionarioProtheus(conn, SUFIXO), FisicoSqlite(conn), conn=conn, dialeto="sqlite")
    finally:
        conn.close()
