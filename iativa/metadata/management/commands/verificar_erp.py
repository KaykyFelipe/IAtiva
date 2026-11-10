"""Verifica a conexão com o ERP real: ambiente, login somente leitura e dicionário.

Lê SOMENTE metadados (versão, permissões do próprio login, estrutura e dicionário). Não lê
linhas de dados. Para se o login tiver qualquer permissão de escrita.

    python manage.py verificar_erp
"""
from django.core.management.base import BaseCommand, CommandError

from iativa.metadata import erp_sqlserver as erp
from django.conf import settings

from iativa.metadata.catalogo import nivel_compartilhamento
from iativa.metadata.dominios import DOMINIOS
from iativa.metadata.fontes import DicionarioProtheus, InformationSchemaSqlServer


class Command(BaseCommand):
    help = "Verifica conexão, login somente leitura e dicionário do ERP real (só metadados)."

    def handle(self, *args, **options):
        try:
            conn = erp.conectar()
        except (erp.ErpNaoConfigurado, ConnectionError) as exc:
            raise CommandError(str(exc)) from None
        try:
            amb = erp.ambiente(conn)
            self.stdout.write(f"Conectado: banco={amb['banco']} versão={amb['versao']} collation={amb['collation']}")

            problemas = erp.verificar_login(conn)
            if problemas:
                raise CommandError("LOGIN NÃO É SOMENTE LEITURA: " + "; ".join(problemas))
            self.stdout.write("Login: somente leitura (sem papéis ou permissões de escrita).")

            sufixos = erp.sufixos_dicionario(conn)
            self.stdout.write("Dicionário SX2 no banco, sufixos: " + (", ".join(sufixos) or "(nenhum)"))
            self.stdout.write(f"SYS_COMPANY (empresas/filiais): {'existe' if erp.tabela_existe(conn, 'SYS_COMPANY') else 'não encontrada'}")

            fisico = InformationSchemaSqlServer(conn)
            escolhido = settings.IATIVA_ERP_SUFIXO
            self.stdout.write(f"Empresa configurada: {escolhido} ({'encontrada' if escolhido in sufixos else 'NÃO encontrada'})")
            for sufixo in [escolhido] if escolhido in sufixos else []:
                faltando = [t for t in ("SX3", "SIX", "SX9") if not erp.tabela_existe(conn, f"{t}{sufixo}")]
                if faltando:
                    self.stdout.write(f"[{sufixo}] dicionário incompleto (faltam {', '.join(faltando)}): ignorado")
                    continue
                dic = DicionarioProtheus(conn, sufixo)
                for dominio, spec in DOMINIOS.items():
                    tabelas = dic.tabelas(list(spec["tabelas"]))
                    self.stdout.write(f"[{sufixo}] {dominio}: {len(tabelas)}/{len(spec['tabelas'])} tabelas no SX2")
                    for alias, t in tabelas.items():
                        campos = dic.campos(alias)
                        colunas = fisico.colunas(t.fisica)
                        reais = [c for c in campos if not c.virtual]
                        sem_fisico = sum(1 for c in reais if c.nome not in colunas)
                        self.stdout.write(
                            f"    {alias} -> {t.fisica}: filial por {nivel_compartilhamento(t)} "
                            f"({t.modo}/{t.modo_unidade}/{t.modo_empresa}); "
                            f"SX3 {len(campos)} campos ({sum(c.personalizado for c in campos)} personalizados, "
                            f"{len(campos) - len(reais)} virtuais, {sem_fisico} sem coluna física); "
                            f"físico {len(colunas)} colunas; chave {'+'.join(dic.chave_primaria(alias)) or '?'}"
                        )
                    rels = dic.relacionamentos(list(tabelas))
                    self.stdout.write(f"    relacionamentos SX9 entre elas: {len(rels)}")
        finally:
            conn.close()
