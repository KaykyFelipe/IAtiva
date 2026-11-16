"""Auditoria de não armazenamento (Etapa 7, R3).

Procura SQL gerado e marcas de linhas do ERP fora dos lugares que as decisões permitem:
- banco: todos os campos de texto/JSON de todos os modelos;
- arquivos: var/ da aplicação (logs, sessões, trabalho da manutenção).

Exceções aceitas por decisão (não contam como achado): o resultado entregue no histórico do chat
(M8/M44, `Mensagem.resultado`) e a conclusão da verificação gravada na tarefa (M29).

A saída diz só ONDE há achado e quantos: nunca o conteúdo, para o relatório não virar ele mesmo um
vazamento. Código de saída 1 quando há achado.
"""
import json
import re
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import models

# SQL gerado: comando com FROM/JOIN, ou as colunas de controle do Protheus que só aparecem em SQL.
SQL = re.compile(r"\bSELECT\b[\s\S]{0,400}?\bFROM\b|\bD_E_L_E_T_\b|\bR_E_C_N_O_\b", re.I)

# Campos onde guardar resultado é permitido por decisão (modelo.campo, rótulo do app).
PERMITIDOS = {
    ("iativa_chat", "mensagem", "resultado"),  # M8/M44: tabela entregue fica no histórico
    ("iativa_agent", "tarefa", "conclusao"),   # M29: conclusão da verificação
}

# Arquivos que podem conter SQL legitimamente (código e migrações não ficam em var/; isto é só var/).
IGNORAR_ARQUIVOS = re.compile(r"(\.sqlite3?(-wal|-shm|-journal)?|\.pyc|\.png|\.jpg|\.gif|\.woff2?|\.zip|\.gz)$", re.I)
MAX_ARQUIVO = 20 * 1024 * 1024


def _texto(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def auditar_banco() -> list[tuple[str, int]]:
    achados = []
    for modelo in apps.get_models():
        if modelo._meta.app_label in ("contenttypes", "admin", "sessions", "auth", "iativa_synthetic_erp"):
            continue
        campos = [f.name for f in modelo._meta.get_fields()
                  if isinstance(f, (models.TextField, models.CharField, models.JSONField))
                  and not f.is_relation
                  and (modelo._meta.app_label, modelo._meta.model_name, f.name) not in PERMITIDOS]
        if not campos:
            continue
        n = {c: 0 for c in campos}
        for linha in modelo.objects.values_list(*campos).iterator(chunk_size=500):
            for c, v in zip(campos, linha):
                if SQL.search(_texto(v)):
                    n[c] += 1
        achados += [(f"{modelo._meta.label}.{c}", q) for c, q in n.items() if q]
    return achados


def auditar_arquivos(raiz: Path) -> list[tuple[str, int]]:
    achados = []
    if not raiz.is_dir():
        return achados
    for arq in raiz.rglob("*"):
        try:
            if (not arq.is_file() or arq.is_symlink() or IGNORAR_ARQUIVOS.search(arq.name)
                    or "/.git/" in arq.as_posix() or "/skills/" in arq.as_posix()
                    or arq.stat().st_size > MAX_ARQUIVO):
                continue
            q = len(SQL.findall(arq.read_text(encoding="utf-8", errors="ignore")))
        except OSError:
            continue
        if q:
            achados.append((str(arq.relative_to(raiz)), q))
    return achados


class Command(BaseCommand):
    help = "Confere que não há SQL gerado nem linhas do ERP guardados fora do permitido (R3)."

    def handle(self, *args, **opts):
        banco = auditar_banco()
        arquivos = auditar_arquivos(Path(settings.BASE_DIR) / "var")
        for onde, q in banco:
            self.stdout.write(f"BANCO {onde}: {q} registro(s) com SQL")
        for onde, q in arquivos:
            self.stdout.write(f"ARQUIVO var/{onde}: {q} trecho(s) com SQL")
        if banco or arquivos:
            self.stdout.write(f"ACHADOS: {len(banco)} campo(s) e {len(arquivos)} arquivo(s)")
            raise SystemExit(1)
        self.stdout.write("OK: nenhum SQL gerado fora do permitido no banco e em var/")
