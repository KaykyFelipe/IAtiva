"""Proteções locais para pedidos explícitos de programação; não confundir códigos do ERP."""
import re
import unicodedata

RECUSA = ('A IAtiva serve para consultar e analisar os dados da empresa dentro das suas permissões. '
          'Não gero códigos, scripts ou programas. Diga qual informação você precisa consultar.')


def pedido_codigo(texto):
    t = unicodedata.normalize('NFKD', texto).encode('ascii', 'ignore').decode().lower()
    artefato = re.search(r'\b(codigo|script|programa|funcao|aplicativo|site|macro)\b', t)
    linguagem = re.search(r'\b(python|javascript|typescript|java|sql|html|css|php|c\+\+|csharp|vba|bash|powershell)\b', t)
    pedido = re.search(r'\b(crie|criar|faca|fazer|gere|gerar|escreva|escrever|programe|programar|desenvolva|implemente)\b', t)
    codigo_generico = re.search(r'\bcodigo\b', t) and not re.search(
        r'\bcodigo\s+(?:d[oa]\s+)?(?:produto|filial|cliente|fornecedor|funcionario|cargo|funcao|tes|empresa)\b', t)
    return bool((artefato and linguagem) or (pedido and (
        codigo_generico or re.search(r'\b(script|programa|macro|sql)\b', t))))


def resposta_codigo(texto):
    return bool(re.search(r'```|^\s*(?:def \w+\(|import \w+|from \w+ import |SELECT .+ FROM |function \w+\()', texto, re.M | re.I))
