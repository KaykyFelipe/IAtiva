"""M85: pedido de dado pessoal protegido é respondido na hora, sem procurar tabela por minutos.

A criação automática de consultas nunca mostra documentos, salário, data de nascimento, contatos, endereço e
dados bancários (`aprendizado.SENSIVEL_RE`). Antes, o modelo não sabia disso e procurava o campo em várias
tabelas até o tempo acabar ("liste os aniversários do mês"). Agora o pedido é reconhecido pelo assunto e a
resposta explica o motivo e o caminho (consulta aprovada pela TI com esse campo).
"""
import re

from .periodo import _norm

# Assunto do pedido -> dado protegido que ele exige.
ASSUNTOS = (
    (r'\baniversari\w*|\bnascimento\b|\bnascid[oa]s?\b|\bidade\b|\bdata de nasc', 'data de nascimento'),
    (r'\bcpf\b|\bcnpj\b|\brg\b|\bpis\b|\bctps\b', 'documento pessoal (CPF, RG, PIS, CTPS)'),
    (r'\bsalari\w*|\bremunera\w*|\bvencimentos? (do|dos|de) funcionari', 'salário'),
    (r'\btelefone\w*|\bcelular\w*|\be-?mail\w*|\bendere[cç]o\w*|\bcep\b', 'contato ou endereço pessoal'),
    (r'\bconta corrente\b|\bagencia bancaria\b|\bdados bancarios\b|\bchave pix\b', 'dado bancário'),
)


def protegido(texto) -> str:
    """O dado protegido que o pedido exige, ou ""."""
    t = _norm(texto)
    return next((nome for padrao, nome in ASSUNTOS if re.search(padrao, t)), "")


def liberado(user, nome) -> bool:
    """Existe consulta aprovada (pela TI) que já traz esse dado: aí o fluxo normal segue."""
    from .contratos import objetivos_para
    from .models import ConsultaModelo
    rotulo = {'data de nascimento': r'NASC'}.get(nome)
    if not rotulo:
        return False
    codigos = [o.codigo for o in objetivos_para(user) if o.modelo_id]
    return any(re.search(rotulo, f"{c.get('campo', '')} {c.get('rotulo', '')}", re.I)
               for cm in ConsultaModelo.objects.filter(codigo__in=codigos, situacao='aprovada')
               for c in cm.spec.get('colunas', []))


def resposta(nome) -> str:
    return (f"Esse pedido precisa de {nome}, que é um dado pessoal protegido: eu não busco nem mostro esse tipo de "
            "informação por conta própria. Para liberar, a TI precisa aprovar uma consulta com esse campo para o "
            "setor responsável. Comunique alguém da TI se for necessário.")
