"""Filtro por campo (opcional) nas consultas aprendidas: "matrícula 000123", "RA_CODFUNC = 0042"."""
from django.db import migrations

DADOS = {
    "nome": "Filtro por campo",
    "descricao": "Valor exato em um campo filtrável da consulta, no formato CAMPO=valor (vários separados "
                 "por ponto e vírgula). Opcional: só quando a pessoa cita um código, matrícula etc.",
    "tipo": "texto",
    "contem_dado_pessoal": True,  # pode ser matrícula: fica só na solicitação, por 30 dias
}


def criar(apps, schema_editor):
    ParameterDefinition = apps.get_model("iativa_memory", "ParameterDefinition")
    ParameterDefinition.objects.get_or_create(codigo="filtro-campo", defaults=DADOS)


class Migration(migrations.Migration):
    dependencies = [("iativa_memory", "0002_definicao_texto_busca")]
    operations = [migrations.RunPython(criar, migrations.RunPython.noop)]
