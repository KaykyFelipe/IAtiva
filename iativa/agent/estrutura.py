"""Cadastro de filiais da plataforma, filtrado pelos acessos vigentes."""
import re
from .periodo import _norm


def solicitado(texto):
    palavras=set(re.findall(r'[a-z]+',_norm(texto)))
    return bool(palavras & {'filiais','filial'} and palavras & {'cadastradas','disponiveis','autorizadas','liberadas'}
        and palavras <= {'ti','quais','filiais','filial','da','empresa','estao','cadastradas','disponiveis',
            'autorizadas','liberadas','mostre','o','codigo','e','nome','de','cada','os','nomes','codigos','na','iativa'})


def consultar(user):
    from iativa.accounts.authz import setores_autorizados,filiais_autorizadas,Permissao
    from iativa.accounts.models import Department
    from iativa.accounts.filiais import nome
    from iativa.query.servico import Resultado
    from .agente import Saida
    setor=Department.objects.filter(pk__in=setores_autorizados(user,Permissao.CONSULTAR),ativo=True).order_by('pk').first()
    if setor is None: return Saida('Seu usuário não tem filiais de consulta autorizadas.',{'tipo':'bloqueada'})
    filiais=sorted(filiais_autorizadas(user,setor))
    r=Resultado(True,'Filiais cadastradas nos acessos da IAtiva e autorizadas para esta conta no setor '+setor.nome+'.',
        colunas=['Código','Nome'],tipos=['texto','texto'],linhas=[[c,nome(c) or 'Nome não cadastrado'] for c in filiais],
        escopo={'setor':setor.pk,'filiais':filiais,'campos':{}},premissas=[
        'Fonte: cadastro de acessos da IAtiva. Não representa uma consulta ao cadastro completo de empresas/filiais do ERP.',
        'Somente filiais autorizadas; código sem nome não é completado por suposição.'])
    return Saida('',{'tipo':'executada'},resultado=r)
