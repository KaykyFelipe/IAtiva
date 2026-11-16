"""Preparação explícita de papéis/áreas; nunca descobre usuários ou filiais do ERP."""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from iativa.accounts.models import AccessPolicy, Department, UserProfile, Funcao


class Command(BaseCommand):
    help = 'Prepara RH/Comercial e concede setores a TI/Diretoria, preservando as filiais já concedidas. Sem --aplicar, só mostra o plano.'

    def add_arguments(self, parser):
        parser.add_argument('--aplicar', action='store_true')
        parser.add_argument('--ti-todos', action='store_true')

    @transaction.atomic
    def handle(self, *args, **options):
        aplicar, ti = options.get('aplicar', False), options.get('ti_todos', False)
        faltantes = [codigo for codigo in ('rh', 'comercial') if not Department.objects.filter(codigo=codigo).exists()]
        perfis = UserProfile.objects.filter(user__is_active=True, department__ativo=True).filter(
            Q(funcao=Funcao.DIRETORIA) | Q(department__codigo='diretoria') |
            (Q(funcao=Funcao.TI) | Q(department__codigo='ti') if ti else Q(pk__in=[]))).select_related('user', 'department')
        planejadas, sem_filiais = [], 0
        agora = timezone.now()
        for perfil in perfis:
            politicas = list(AccessPolicy.objects.filter(user=perfil.user, ativo=True, valido_de__lte=agora)
                             .filter(Q(valido_ate__isnull=True) | Q(valido_ate__gt=agora))
                             .filter(Q(department__isnull=True) | Q(department__ativo=True)))
            filiais = sorted({f for p in politicas if p.pode_consultar for f in p.filiais})
            if not filiais:
                sem_filiais += 1
                continue
            papel = Funcao.DIRETORIA if perfil.funcao == Funcao.DIRETORIA or perfil.department.codigo == 'diretoria' else Funcao.TI
            aprovador = papel == Funcao.DIRETORIA or perfil.user.is_superuser or any(p.pode_administrar or p.pode_aprovar_conhecimento for p in politicas)
            planejadas.append((perfil, filiais, papel, aprovador))
        self.stdout.write(f'setores_a_criar={len(faltantes)} contas_com_filiais={len(planejadas)} contas_sem_filiais={sem_filiais} aplicar={aplicar}')
        if not aplicar:
            return
        for codigo, nome in [('rh', 'Recursos Humanos'), ('comercial', 'Comercial')]:
            Department.objects.get_or_create(codigo=codigo, defaults={'nome': nome})
        for perfil, filiais, papel, aprovador in planejadas:
            if perfil.funcao != papel:
                perfil.funcao = papel
                perfil.save(update_fields=['funcao'])
            for setor in Department.objects.filter(ativo=True):
                existentes = AccessPolicy.objects.filter(user=perfil.user, department=setor, ativo=True, pode_consultar=True,
                    valido_de__lte=agora).filter(Q(valido_ate__isnull=True) | Q(valido_ate__gt=agora))
                # Uma concessão existente pode ser deliberadamente mais estreita.
                # Só os novos setores herdam a união de filiais já autorizadas.
                novas = [] if existentes.exists() else filiais
                if novas:
                    AccessPolicy.objects.create(user=perfil.user, department=setor, pode_consultar=True,
                        pode_ler_memoria=True, filiais=novas)
            if aprovador and not AccessPolicy.objects.filter(user=perfil.user, department__isnull=True, ativo=True,
                pode_aprovar_conhecimento=True, valido_de__lte=agora).filter(Q(valido_ate__isnull=True) | Q(valido_ate__gt=agora)).exists():
                AccessPolicy.objects.create(user=perfil.user, pode_aprovar_conhecimento=True, pode_ler_memoria=True)
        self.stdout.write('Preparação concluída. Nenhuma filial nova foi inferida; contas sem filiais exigem concessão explícita.')
