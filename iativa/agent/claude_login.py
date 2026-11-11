"""Login pessoal conduzido pela CLI oficial, com fluxo efêmero vinculado à sessão.

Adapta OpenMausBot/server/drivers/claude-login-auth.ts (Apache-2.0).
Credenciais são escritas somente pela CLI, no diretório exclusivo do usuário.
"""
import atexit
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
from threading import RLock, Thread, Timer
from urllib.parse import urlsplit
import uuid

from django.conf import settings
from django.core.exceptions import PermissionDenied

from .claude_code import ClaudeCodeProvider
from .provedores import FalhaModelo


LOGIN_HOSTS = {"claude.ai", "claude.com", "console.anthropic.com", "platform.claude.com"}
ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
CODIGO = re.compile(r"[A-Za-z0-9_\-#.:]{8,1024}")
MAX_OUTPUT = 32_768
TEMPO_LOGIN = 900


MODELOS = (("opus", "Opus (mais capaz)"), ("sonnet", "Sonnet (equilibrado)"), ("haiku", "Haiku (mais rápido)"))
ESFORCOS = (("", "Padrão da CLI"), ("low", "Baixo"), ("medium", "Médio"), ("high", "Alto"),
            ("xhigh", "Muito alto"), ("max", "Máximo"))
PREFERENCIAS = "iativa-preferencias.json"


def eh_ti(user):
    """Só quem tem a função TI no perfil vê e usa a conexão Claude."""
    from iativa.accounts.models import Funcao

    perfil = getattr(user, "perfil_iativa", None) if user.is_authenticated else None
    return bool(perfil and perfil.funcao == Funcao.TI)


def permitido(user):
    dono = settings.IATIVA_AGENTE.get("claude_usuario", "")
    return bool(user.is_authenticated and user.is_active and eh_ti(user)
                and (not dono or user.get_username() == dono))


def config_usuario(user):
    if not permitido(user):
        raise PermissionDenied
    cfg = dict(settings.IATIVA_AGENTE)
    root = Path(cfg.get("claude_contas_dir") or Path(settings.BASE_DIR) / "var/claude-pessoal")
    if not root.is_absolute():
        raise FalhaModelo("O diretório das contas Claude deve ser absoluto.")
    cfg["claude_config_dir"] = str(root / f"usuario-{user.pk}")
    cfg.update(preferencias(cfg))
    return cfg


def preferencias(cfg):
    """Modelo e esforço escolhidos pelo usuário; padrão do settings quando não há escolha válida."""
    atual = {"claude_modelo": cfg.get("claude_modelo", "sonnet"), "claude_esforco": cfg.get("claude_esforco", "")}
    try:
        dados = json.loads((Path(cfg["claude_config_dir"]) / PREFERENCIAS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return atual
    if isinstance(dados, dict):
        if dados.get("modelo") in dict(MODELOS):
            atual["claude_modelo"] = dados["modelo"]
        if dados.get("esforco") in dict(ESFORCOS):
            atual["claude_esforco"] = dados["esforco"]
    return atual


def salvar_preferencias(user, modelo, esforco):
    if modelo not in dict(MODELOS) or esforco not in dict(ESFORCOS):
        raise FalhaModelo("Modelo ou esforço inválido.")
    pasta = Path(config_usuario(user)["claude_config_dir"])
    pasta.mkdir(parents=True, exist_ok=True, mode=0o700)
    (pasta / PREFERENCIAS).write_text(json.dumps({"modelo": modelo, "esforco": esforco}), encoding="utf-8")


def ativa(user):
    if not permitido(user):
        return False
    return (Path(config_usuario(user)["claude_config_dir"]) / "iativa-ativado").is_file()


def _marcar(cfg, habilitado):
    marker = Path(cfg["claude_config_dir"]) / "iativa-ativado"
    if habilitado:
        marker.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        marker.touch(mode=0o600)
    else:
        marker.unlink(missing_ok=True)


def link_oficial(texto):
    if "\n" not in texto:
        return None
    completo = ANSI.sub("", texto).rsplit("\n", 1)[0]
    for candidate in re.findall(r"https://[^\s\x00-\x1f]+", completo):
        candidate = candidate.rstrip("),.;")
        try:
            url = urlsplit(candidate)
            host = (url.hostname or "").lower()
            if (url.scheme == "https" and host in LOGIN_HOSTS and not url.username
                    and not url.password and url.port in (None, 443) and "/oauth/" in url.path):
                return candidate
        except ValueError:
            pass
    return None


def chave_sessao(request):
    return hashlib.sha256(request.session.session_key.encode()).hexdigest()


def provedor_login(user):
    return ClaudeCodeProvider(config_usuario(user), somente_login=True)


def conectado(provider):
    code, text, _ = provider._executar(["--safe-mode", "auth", "status", "--json"], timeout=10)
    try:
        status = json.loads(text)
    except (ValueError, TypeError):
        raise FalhaModelo("Não foi possível conferir a conexão Claude.") from None
    if not isinstance(status, dict):
        raise FalhaModelo("Estado de autenticação Claude inválido.")
    if not code and status.get("loggedIn") is True:
        if status.get("authMethod") != "claude.ai":
            raise FalhaModelo("Esta conexão usa API ou autenticação alternativa; entre com sua assinatura Claude.")
        return True
    if code in (0, 1) and status.get("loggedIn") is False:
        return False
    raise FalhaModelo("A CLI não confirmou o estado da conexão Claude.")


@dataclass
class Fluxo:
    dono: int
    sessao: str
    cfg: dict
    provider: object
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    fase: str = "iniciando"
    url: str = ""
    mensagem: str = "Preparando o login oficial do Claude…"
    proc: object = None
    enviou: bool = False
    lock: object = field(default_factory=RLock)
    temporizador: object = None

    def estado(self):
        with self.lock:
            return {"id": self.id, "fase": self.fase, "url": self.url,
                    "mensagem": self.mensagem, "aguardando": self.fase in ("iniciando", "aguardando", "confirmando")}

    def terminar(self, fase, mensagem):
        with self.lock:
            if self.fase not in ("iniciando", "aguardando", "confirmando"):
                return
            self.fase, self.mensagem, self.url = fase, mensagem, ""
            if self.temporizador:
                self.temporizador.cancel()
            if self.proc and self.proc.poll() is None:
                try:
                    self.proc.terminate()
                    self.proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait(timeout=2)
                except OSError:
                    pass

    def rodar(self):
        output = ""
        try:
            self.provider.checar_cli()
            if conectado(self.provider):
                with self.lock:
                    if self.fase == "iniciando":
                        _marcar(self.cfg, True)
                        self.terminar("conectado", "Claude conectado para suas conversas pessoais.")
                return
            folder = Path(self.cfg["claude_config_dir"])
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            env = {**self.provider.env, "BROWSER": "true"} if os.name != "nt" else self.provider.env
            with TemporaryDirectory(prefix="iativa-login-") as cwd:
                with self.lock:
                    if self.fase != "iniciando":
                        return
                    self.proc = subprocess.Popen(
                        [self.provider.cli, "--safe-mode", "auth", "login"],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        cwd=cwd, env=env, shell=False, bufsize=0,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    )
                while chunk := os.read(self.proc.stdout.fileno(), 1024):
                    output += chunk.decode("utf-8", errors="replace")
                    if len(output) > MAX_OUTPUT:
                        self.terminar("falhou", "A CLI retornou uma resposta inesperada. Atualize o Claude Code.")
                        return
                    url = link_oficial(output)
                    with self.lock:
                        if self.fase == "iniciando" and url:
                            self.url, self.fase = url, "aguardando"
                            self.mensagem = "Abra o login do Claude e cole o código mostrado na página oficial."
                            self.temporizador.cancel()
                            self.temporizador = Timer(TEMPO_LOGIN, lambda: self.terminar("expirou", "O login expirou. Conecte novamente."))
                            self.temporizador.daemon = True
                            self.temporizador.start()
                code = self.proc.wait(timeout=2)
                with self.lock:
                    if self.fase not in ("iniciando", "aguardando", "confirmando"):
                        return
                    if code == 0 and conectado(self.provider):
                        _marcar(self.cfg, True)
                        self.terminar("conectado", "Claude conectado para suas conversas pessoais.")
                    else:
                        self.terminar("falhou", "O Claude não confirmou o login. Inicie uma nova conexão.")
        except Exception:
            self.terminar("falhou", "Não foi possível concluir o login. Verifique a instalação do Claude Code.")
        finally:
            output = ""
            if self.proc:
                for stream in (self.proc.stdin, self.proc.stdout):
                    try:
                        stream.close()
                    except OSError:
                        pass


class LoginPessoal:
    def __init__(self):
        self.lock = RLock()
        self.fluxos = {}

    def iniciar(self, user, sessao):
        cfg = config_usuario(user)
        with self.lock:
            antigo = self.fluxos.get(user.pk)
            if antigo and antigo.estado()["aguardando"]:
                if antigo.sessao != sessao:
                    raise FalhaModelo("Já existe um login em andamento em outra sessão sua.")
                return antigo.estado()
            fluxo = Fluxo(user.pk, sessao, cfg, provedor_login(user))
            self.fluxos[user.pk] = fluxo
            fluxo.temporizador = Timer(min(30, TEMPO_LOGIN), lambda: fluxo.terminar("expirou", "O login não respondeu a tempo. Conecte novamente."))
            fluxo.temporizador.daemon = True
            fluxo.temporizador.start()
            Thread(target=fluxo.rodar, daemon=True).start()
            return fluxo.estado()

    def obter(self, user, sessao, id):
        config_usuario(user)
        with self.lock:
            fluxo = self.fluxos.get(user.pk)
            if not fluxo or fluxo.id != id or fluxo.sessao != sessao:
                raise FalhaModelo("Este login expirou ou pertence a outra sessão. Conecte novamente.")
            return fluxo

    def concluir(self, user, sessao, id, codigo):
        fluxo = self.obter(user, sessao, id)
        codigo = codigo.strip()
        if not CODIGO.fullmatch(codigo):
            raise FalhaModelo("Cole o código completo exibido pelo Claude, sem espaços ou quebras de linha.")
        with fluxo.lock:
            if fluxo.fase != "aguardando" or fluxo.enviou or not fluxo.proc or fluxo.proc.poll() is not None:
                raise FalhaModelo("O código já foi enviado ou o login não está mais disponível.")
            fluxo.enviou = True
            fluxo.fase, fluxo.url = "confirmando", ""
            fluxo.mensagem = "Confirmando o login com o Claude…"
            fluxo.temporizador.cancel()
            fluxo.temporizador = Timer(60, lambda: fluxo.terminar("falhou", "O Claude não confirmou o código a tempo. Conecte novamente."))
            fluxo.temporizador.daemon = True
            fluxo.temporizador.start()
            try:
                fluxo.proc.stdin.write((codigo + "\n").encode("utf-8"))
                fluxo.proc.stdin.flush()
            except OSError:
                fluxo.terminar("falhou", "Não foi possível entregar o código ao Claude.")
            finally:
                codigo = ""
        return fluxo.estado()

    def cancelar(self, user, sessao, id):
        fluxo = self.obter(user, sessao, id)
        fluxo.terminar("cancelado", "Conexão cancelada.")
        return fluxo.estado()

    def desconectar(self, user, sessao):
        cfg = config_usuario(user)
        with self.lock:
            fluxo = self.fluxos.get(user.pk)
            if fluxo and fluxo.estado()["aguardando"]:
                if fluxo.sessao != sessao:
                    raise FalhaModelo("Cancele o login na sessão que o iniciou antes de desconectar.")
                fluxo.terminar("cancelado", "Conexão cancelada.")
            _marcar(cfg, False)
            provider = provedor_login(user)
            code, _, _ = provider._executar(["--safe-mode", "auth", "logout"], timeout=10)
            if code or conectado(provider):
                raise FalhaModelo("O uso no chat foi desativado, mas a CLI não confirmou a desconexão. Tente novamente.")

    def fechar(self):
        with self.lock:
            for fluxo in self.fluxos.values():
                fluxo.terminar("cancelado", "Aplicação reiniciada; conecte novamente.")


login = LoginPessoal()
atexit.register(login.fechar)
