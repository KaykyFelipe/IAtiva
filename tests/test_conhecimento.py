"""Documentos de conhecimento: cabeçalho, aprovação, conteúdo proibido e busca."""
from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from iativa.knowledge.busca import MAX_CARACTERES, MAX_TRECHOS, buscar, normalizar
from iativa.knowledge.documentos import carregar_todos

CABECALHO = """---
titulo: {titulo}
dominio: financeiro_receber
origem: sintetico
versao: 1
responsavel: teste
situacao: {situacao}
aprovado_por: revisor
aprovado_em: 2026-11-10
{extra}
---
"""


def _doc(raiz, nome, corpo, titulo="Doc", situacao="aprovado", extra=""):
    path = raiz / nome
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(CABECALHO.format(titulo=titulo, situacao=situacao, extra=extra) + corpo, encoding="utf-8")
    return path


def test_documentos_do_repositorio_sao_validos():
    saida = StringIO()
    call_command("validar_conhecimento", stdout=saida)
    assert "com_problema=0" in saida.getvalue()
    assert all(not d.problemas for d in carregar_todos())  # rascunhos são válidos, só não recuperáveis


def test_readme_sem_cabecalho_e_ignorado(tmp_path):
    (tmp_path / "README.md").write_text("# Leia-me\n", encoding="utf-8")
    assert carregar_todos(tmp_path) == []


def test_rascunho_e_incompleto_nao_sao_recuperaveis(tmp_path):
    _doc(tmp_path, "a.md", "## X\ntexto", situacao="rascunho")
    (tmp_path / "b.md").write_text("---\ntitulo: B\nsituacao: aprovado\n---\n## Y\ntexto", encoding="utf-8")
    docs = {d.caminho: d for d in carregar_todos(tmp_path)}
    assert not docs["a.md"].recuperavel and not docs["a.md"].problemas
    assert not docs["b.md"].recuperavel
    assert "falta aprovado_por" in docs["b.md"].problemas


@pytest.mark.parametrize("conteudo,problema", [
    ("CPF 123.456.789-09", "cpf"),
    ("CNPJ 12.345.678/0001-90", "cnpj"),
    ("senha: abc123", "credencial"),
    ("Server=sql01;Uid=leitura;", "conexao"),
])
def test_conteudo_proibido_recusa_o_documento(tmp_path, conteudo, problema):
    _doc(tmp_path, "x.md", f"## Seção\n{conteudo}\n")
    (doc,) = carregar_todos(tmp_path)
    assert f"conteúdo proibido: {problema}" in doc.problemas and not doc.recuperavel


def test_comando_falha_com_problema(tmp_path, settings):
    settings.IATIVA_CONHECIMENTO_DIR = tmp_path
    _doc(tmp_path, "x.md", "## S\nsenha: 1\n")
    with pytest.raises(CommandError):
        call_command("validar_conhecimento", stdout=StringIO())


def test_normalizacao_ignora_acentos_e_plural():
    assert normalizar("Títulos Vencidos de Clientes") == ["titulo", "vencido", "cliente"]


def test_busca_traz_so_trechos_relevantes(tmp_path):
    _doc(tmp_path, "a.md", "## Baixa parcial\nSaldo diminui na baixa parcial.\n## Outro\nNada a ver.", titulo="A")
    _doc(tmp_path, "b.md", "## Filial\nCadastro compartilhado entre filiais.", titulo="B")
    trechos = buscar("como funciona baixa parcial?", carregar_todos(tmp_path))
    assert [t.secao for t in trechos] == ["Baixa parcial"]
    assert trechos[0].aprovado_por == "revisor" and trechos[0].versao == "1"


def test_busca_limita_quantidade_e_tamanho(tmp_path):
    corpo = "".join(f"## S{i}\ntitulo saldo {'x' * 3000}\n" for i in range(10))
    _doc(tmp_path, "a.md", corpo)
    trechos = buscar("saldo", carregar_todos(tmp_path), limite=50)
    assert len(trechos) == MAX_TRECHOS
    assert all(len(t.texto) <= MAX_CARACTERES for t in trechos)


def test_busca_vazia(tmp_path):
    _doc(tmp_path, "a.md", "## S\ntexto")
    assert buscar("de para com", carregar_todos(tmp_path)) == []
