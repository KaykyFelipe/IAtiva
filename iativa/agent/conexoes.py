"""Seleção explícita do provedor, independente por agente; não contém credenciais."""
import json
from pathlib import Path
from tempfile import NamedTemporaryFile

from django.conf import settings
from django.core.exceptions import PermissionDenied

from .provedores import FalhaModelo


def gravar_json(path, valor):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = None
    try:
        with NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as f:
            tmp = Path(f.name)
            json.dump(valor, f, ensure_ascii=False)
        tmp.replace(path)
    finally:
        if tmp:
            tmp.unlink(missing_ok=True)


def pasta():
    root = Path(getattr(settings, 'IATIVA_CONEXOES_DIR', Path(settings.BASE_DIR) / 'var/conexoes'))
    if not root.is_absolute():
        raise FalhaModelo('O diretório das seleções de provedor deve ser absoluto.')
    return root


def arquivo(user, perfil):
    from .claude_login import permitido, perfil_valido
    if not permitido(user):
        raise PermissionDenied
    perfil_valido(perfil)
    return pasta() / ('manutencao.json' if perfil == 'manutencao' else f'atendimento-{user.pk}.json')


def preferido(user, perfil='atendimento'):
    try:
        dado = json.loads(arquivo(user, perfil).read_text(encoding='utf-8'))
    except PermissionDenied:
        return ''
    except (OSError, ValueError):
        dado = {}
    p = dado.get('provedor') if isinstance(dado, dict) else None
    if p in ('claude', 'chatgpt'):
        return p
    return ''


def selecionado(user, perfil='atendimento'):
    p = preferido(user, perfil)
    if p:
        return p
    from . import claude_login, codex_login
    if claude_login.ativa(user, perfil):
        return 'claude'
    return 'chatgpt' if codex_login.ativa(user, perfil) else ''


def escolher(user, perfil, provedor):
    path = arquivo(user, perfil)
    if provedor not in ('claude', 'chatgpt'):
        raise FalhaModelo('Provedor inválido.')
    if provedor == 'claude':
        from .claude_login import provedor_login, conectado, config_usuario, _marcar
        ok = conectado(provedor_login(user, perfil))
        _marcar(config_usuario(user, perfil), ok)
    else:
        from .codex_login import verificar
        ok = verificar(user, perfil)
    if not ok:
        raise FalhaModelo('Conecte e verifique a conta antes de selecionar esse provedor.')
    gravar_json(path, {'provedor': provedor})
