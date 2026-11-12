"""Conta ChatGPT por device code oficial; fluxo efêmero por usuário/sessão/agente."""
import atexit
import json
from pathlib import Path
import re
from threading import RLock, Thread
import time
from urllib.parse import urlsplit
import uuid

from django.conf import settings
from django.core.exceptions import PermissionDenied

from .claude_login import permitido, perfil_valido
from .codex_rpc import AppServer
from .conexoes import gravar_json
from .provedores import FalhaModelo

ID_MODELO = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$')
ESFORCOS = ('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra')


def pasta(user, perfil='atendimento'):
    if not permitido(user):
        raise PermissionDenied
    perfil_valido(perfil)
    root = Path(settings.IATIVA_AGENTE.get('codex_contas_dir') or Path(settings.BASE_DIR) / 'var/chatgpt')
    if not root.is_absolute():
        raise FalhaModelo('O diretório das contas ChatGPT deve ser absoluto.')
    return root / ('manutencao' if perfil == 'manutencao' else f'usuario-{user.pk}')


def rpc(user, perfil='atendimento'):
    return AppServer(pasta(user, perfil), cli=settings.IATIVA_AGENTE.get('codex_cli', 'codex'))


def marcar(user, perfil, ativo):
    path = pasta(user, perfil) / 'iativa-ativado'
    if ativo:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.touch(mode=0o600)
    else:
        path.unlink(missing_ok=True)


def ativa(user, perfil='atendimento'):
    return permitido(user) and (pasta(user, perfil) / 'iativa-ativado').is_file()


def ler_json(path, padrao):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return padrao


def modelos(user, perfil):
    dados = ler_json(pasta(user, perfil) / 'iativa-modelos.json', [])
    return dados if isinstance(dados, list) else []


def atualizar_modelos(user, perfil, server=None):
    def ler(s):
        if not s.autenticado():
            raise FalhaModelo('Conecte a conta ChatGPT antes de atualizar os modelos.')
        out, cursor = [], None
        for _ in range(5):
            res = s.request('model/list', {'limit': 100, 'includeHidden': False, 'cursor': cursor})
            for m in res.get('data', []):
                id_ = m.get('model', '')
                if isinstance(id_, str) and ID_MODELO.fullmatch(id_) and not m.get('hidden'):
                    esforcos = [e['reasoningEffort'] for e in m.get('supportedReasoningEfforts', [])
                                if isinstance(e, dict) and e.get('reasoningEffort') in ESFORCOS]
                    out.append({'id': id_, 'nome': str(m.get('displayName') or id_)[:100], 'esforcos': esforcos})
            cursor = res.get('nextCursor')
            if not cursor:
                break
        gravar_json(pasta(user, perfil) / 'iativa-modelos.json', out)
        return out
    if server is not None:
        return ler(server)
    with rpc(user, perfil) as s:
        return ler(s)


def preferencias(user, perfil):
    dados = ler_json(pasta(user, perfil) / 'iativa-preferencias.json', {})
    if not isinstance(dados, dict):
        dados = {}
    m, e = dados.get('modelo', ''), dados.get('esforco', '')
    return {'modelo': m if isinstance(m, str) and (not m or ID_MODELO.fullmatch(m)) else '',
            'esforco': e if e in ESFORCOS else ''}


def salvar_preferencias(user, perfil, modelo, esforco):
    catalogo = modelos(user, perfil)
    item = next((m for m in catalogo if m.get('id') == modelo), None)
    if modelo and item is None:
        raise FalhaModelo('Escolha um modelo do catálogo da conta conectada. Use Atualizar modelos.')
    if esforco and (not item or esforco not in item.get('esforcos', [])):
        raise FalhaModelo('Esse esforço não está disponível para o modelo escolhido.')
    gravar_json(pasta(user, perfil) / 'iativa-preferencias.json', {'modelo': modelo, 'esforco': esforco})


def verificar(user, perfil):
    with rpc(user, perfil) as s:
        ok = s.autenticado()
        marcar(user, perfil, ok)
        return ok


def cartao(user, perfil):
    lista = modelos(user, perfil)
    pref = preferencias(user, perfil)
    return {'ativo': ativa(user, perfil), 'modelos': lista, 'preferencias': pref,
            'esforcos': sorted({e for m in lista for e in m.get('esforcos', [])}), 'estado': None, 'erro': ''}


def link_oficial(url):
    try:
        p = urlsplit(url)
        return bool(p.scheme == 'https' and p.hostname == 'auth.openai.com' and p.path == '/codex/device'
                    and not p.username and not p.password and p.port in (None, 443) and not p.query and not p.fragment)
    except (ValueError, TypeError):
        return False


class Fluxo:
    def __init__(self, user, sessao, perfil):
        self.user, self.sessao, self.perfil = user, sessao, perfil
        self.id = str(uuid.uuid4())
        self.lock = RLock()
        self.fase, self.url, self.codigo = 'iniciando', '', ''
        self.mensagem = 'Preparando o login oficial do ChatGPT…'
        self.server, self.login_id = None, None

    def estado(self):
        with self.lock:
            return {'id': self.id, 'fase': self.fase, 'url': self.url, 'codigo': self.codigo,
                    'mensagem': self.mensagem, 'aguardando': self.fase in ('iniciando', 'aguardando')}

    def fim(self, fase, mensagem):
        with self.lock:
            if self.fase not in ('iniciando', 'aguardando'):
                return
            self.fase, self.mensagem, self.url, self.codigo = fase, mensagem, '', ''

    def rodar(self):
        try:
            with rpc(self.user, self.perfil) as s:
                self.server = s
                if not self.estado()['aguardando']:
                    return
                if s.autenticado():
                    with self.lock:
                        if not self.estado()['aguardando']:
                            return
                        marcar(self.user, self.perfil, True)
                        self.fim('conectado', 'Conta ChatGPT conectada para este agente.')
                    try:
                        atualizar_modelos(self.user, self.perfil, s)
                    except FalhaModelo:
                        pass
                    return
                res = s.request('account/login/start', {'type': 'chatgptDeviceCode'})
                url, codigo = res.get('verificationUrl', ''), res.get('userCode', '')
                if res.get('type') != 'chatgptDeviceCode' or not link_oficial(url) or not re.fullmatch(r'[A-Za-z0-9-]{4,32}', codigo):
                    raise FalhaModelo('O Codex não forneceu um login oficial válido.')
                self.login_id = res.get('loginId')
                with self.lock:
                    if not self.estado()['aguardando']:
                        return
                    self.fase, self.url, self.codigo = 'aguardando', url, codigo
                    self.mensagem = 'Abra a página oficial e digite o código lá. A conexão será confirmada automaticamente.'
                fim = time.monotonic() + 900
                while self.estado()['aguardando'] and time.monotonic() < fim:
                    try:
                        ev = s.evento(2)
                    except FalhaModelo as exc:
                        if exc.temporaria:
                            continue
                        raise
                    if ev.get('method') == 'account/login/completed' and ev.get('params', {}).get('loginId') == self.login_id:
                        if not ev['params'].get('success') or not s.autenticado():
                            raise FalhaModelo('O ChatGPT não confirmou o login. Confira se o login por código está habilitado na conta.')
                        with self.lock:
                            if not self.estado()['aguardando']:
                                return
                            marcar(self.user, self.perfil, True)
                            self.fim('conectado', 'Conta ChatGPT conectada para este agente.')
                        try:
                            atualizar_modelos(self.user, self.perfil, s)
                        except FalhaModelo:
                            pass
                        return
                self.fim('expirou', 'O login expirou. Conecte novamente.')
        except Exception:
            self.fim('falhou', 'Não foi possível concluir o login ChatGPT. Confira a CLI e habilite login por código nas configurações de segurança da conta.')
        finally:
            with self.lock:
                self.codigo, self.url, self.server = '', '', None


class LoginChatGPT:
    def __init__(self):
        self.lock, self.fluxos = RLock(), {}

    def iniciar(self, user, sessao, perfil):
        pasta(user, perfil)
        with self.lock:
            for (uid, p), f in self.fluxos.items():
                if p == perfil and f.estado()['aguardando'] and (uid == user.pk or perfil == 'manutencao'):
                    if uid == user.pk and sessao == f.sessao:
                        return f.estado()
                    raise FalhaModelo('Já há um login deste agente em outra sessão. Aguarde ou cancele na sessão de origem.')
            f = Fluxo(user, sessao, perfil)
            self.fluxos[(user.pk, perfil)] = f
            Thread(target=f.rodar, daemon=True).start()
            return f.estado()

    def obter(self, user, sessao, id_, perfil):
        pasta(user, perfil)
        with self.lock:
            f = self.fluxos.get((user.pk, perfil))
            if not f or f.id != id_ or f.sessao != sessao:
                raise FalhaModelo('Login expirado ou pertencente a outra sessão.')
            return f

    def cancelar(self, user, sessao, id_, perfil):
        f = self.obter(user, sessao, id_, perfil)
        f.fim('cancelado', 'Conexão ChatGPT cancelada.')
        if f.server:
            f.server.close()
        return f.estado()

    def desconectar(self, user, sessao, perfil):
        pasta(user, perfil)
        with self.lock:
            for (uid, p), f in self.fluxos.items():
                if p == perfil and (uid == user.pk or perfil == 'manutencao') and f.estado()['aguardando']:
                    raise FalhaModelo('Cancele o login em andamento na sessão de origem antes de desconectar.')
            marcar(user, perfil, False)
            with rpc(user, perfil) as s:
                s.request('account/logout')

    def fechar(self):
        for f in list(self.fluxos.values()):
            f.fim('cancelado', 'Aplicação reiniciada; conecte novamente.')
            if f.server:
                f.server.close()


login = LoginChatGPT()
atexit.register(login.fechar)
