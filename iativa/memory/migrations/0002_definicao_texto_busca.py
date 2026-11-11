"""Busca por nome (opcional) em todas as consultas: "a Alcans lançou conta mês passado?"."""
from django.db import migrations

DADOS = {
    "nome": "Busca por nome",
    "descricao": "Parte do nome (cliente, fornecedor, funcionário, produto) para filtrar o resultado. "
                 "Opcional: só usado quando a pessoa cita um nome.",
    "tipo": "texto",
    "contem_dado_pessoal": True,  # pode ser nome de pessoa: fica só na solicitação, por 30 dias
}


def criar(apps, schema_editor):
    ParameterDefinition = apps.get_model("iativa_memory", "ParameterDefinition")
    ParameterDefinition.objects.get_or_create(codigo="texto-busca", defaults=DADOS)


class Migration(migrations.Migration):
    dependencies = [("iativa_memory", "0001_initial")]
    operations = [migrations.RunPython(criar, migrations.RunPython.noop)]
