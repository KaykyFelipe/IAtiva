"""Claude Code pessoal via CLI oficial; histórico pertence ao Django, nunca à CLI.

Adaptado do padrão de chamadas isoladas de OpenMausBot (Apache-2.0),
server/drivers/claude-background.ts. Sem copiar credenciais entre aplicações.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from threading import Thread

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from . import decisao, esquema
from .provedores import (
    FalhaModelo, LLMProvider, INSTRUCOES, INSTRUCOES_AGENTE,
    _catalogo, _codigos, _contexto_agente, _renderizar,
)


MAX_SAIDA = 1_000_000
FLAGS = ("--safe-mode", "--tools", "--no-session-persistence",
         "--strict-mcp-config", "--mcp-config", "--json-schema", "--disallowedTools")
ENV_PERMITIDO = {
    "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP",
    "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "PROGRAMFILES",
    "LANG", "LC_ALL", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS",
    "CLAUDE_CODE_GIT_BASH_PATH",
}


def ambiente(config_dir=""):
    # O processo do modelo não recebe senhas do ERP, chaves de API ou rotas herdadas.
    env = {k: v for k, v in os.environ.items() if k.upper() in ENV_PERMITIDO}
    if config_dir:
        path = Path(config_dir).expanduser()
        if not path.is_absolute():
            raise ImproperlyConfigured("claude_config_dir deve ser um caminho absoluto.")
        env["CLAUDE_CONFIG_DIR"] = str(path)
    env.update(CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1", NO_COLOR="1")
    return env


def resolver_cli(configurado):
    path = shutil.which(configurado)
    if not path:
        candidate = Path(configurado).expanduser()
        if candidate.is_absolute() and candidate.is_file():
            path = str(candidate)
    if not path and configurado == "claude":
        home = Path.home()
        nomes = ("claude.exe",) if os.name == "nt" else ("claude",)
        for pasta in (home / ".local/bin", home / ".claude/local", home / "bin"):
            for nome in nomes:
                candidate = pasta / nome
                if candidate.is_file():
                    path = str(candidate)
                    break
            if path:
                break
        if not path and os.name == "nt":
            local = Path(os.environ.get("LOCALAPPDATA", str(home / "AppData/Local")))
            candidates = list((local / "Claude-Data").glob("*/claude-code/*/*/claude.exe"))

            def version(p):
                return tuple(int(n) if n.isdigit() else 0 for n in p.parents[1].name.split("."))

            candidates.sort(key=version, reverse=True)
            path = next((str(p) for p in candidates if p.is_file()), None)
    if not path:
        raise ImproperlyConfigured("Claude Code não encontrado. Instale a CLI ou configure IATIVA_CLAUDE_CLI.")
    if Path(path).suffix.lower() in (".cmd", ".bat", ".ps1"):
        raise ImproperlyConfigured("Configure o executável nativo claude.exe; scripts de shell não são aceitos.")
    return path


def executar_cli(comando, prompt, *, env, timeout):
    with TemporaryDirectory(prefix="iativa-claude-") as cwd:
        try:
            proc = subprocess.Popen(
                comando, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=cwd, env=env, shell=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except OSError:
            raise FalhaModelo("Não foi possível iniciar o Claude Code.") from None
        buffers = [bytearray(), bytearray()]
        excedeu = []

        def ler(stream, destino):
            with stream:
                while chunk := stream.read(4096):
                    if len(destino) + len(chunk) > MAX_SAIDA:
                        excedeu.append(True)
                        proc.kill()
                        return
                    destino.extend(chunk)

        def escrever():
            try:
                with proc.stdin:
                    proc.stdin.write(prompt.encode("utf-8"))
            except (BrokenPipeError, OSError):
                pass

        threads = [Thread(target=ler, args=(proc.stdout, buffers[0]), daemon=True),
                   Thread(target=ler, args=(proc.stderr, buffers[1]), daemon=True),
                   Thread(target=escrever, daemon=True)]
        for thread in threads:
            thread.start()
        expirou = False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            expirou = True
            proc.kill()
            proc.wait()
        finally:
            for thread in threads:
                thread.join(timeout=2)
        if expirou:
            raise FalhaModelo("Claude Code excedeu o tempo limite.", temporaria=True)
        if excedeu or any(t.is_alive() for t in threads):
            raise FalhaModelo("Saída do Claude Code excedeu os limites.", temporaria=True)
        return proc.returncode, buffers[0].decode("utf-8", errors="replace"), buffers[1].decode("utf-8", errors="replace")


def _objeto(texto):
    try:
        valor = json.loads(texto)
    except (ValueError, TypeError):
        raise FalhaModelo("Claude Code devolveu JSON inválido.") from None
    if not isinstance(valor, dict):
        raise FalhaModelo("Claude Code devolveu estrutura inválida.")
    return valor


def _falha(texto):
    texto = texto.lower()
    if any(t in texto for t in ("rate_limit", "rate limit", "usage limit", "hit your limit", "429", "overloaded")):
        return FalhaModelo("Limite da assinatura ou capacidade do Claude atingido; tente mais tarde.", temporaria=True)
    if any(t in texto for t in ("not logged", "authentication", "unauthorized", "401", "oauth")):
        return FalhaModelo("Login Claude inválido ou expirado; reconecte em Conectar Claude.", temporaria=True)
    if any(t in texto for t in ("billing", "credit balance", "extra usage")):
        return FalhaModelo("Claude recusou o uso por limite ou cobrança. Nenhum fallback foi ativado.", temporaria=True)
    return FalhaModelo("O processo Claude Code falhou.", temporaria=True)


class ClaudeCodeProvider(LLMProvider):
    nome = "claude-code"

    def __init__(self, config=None, *, somente_login=False):
        cfg = settings.IATIVA_AGENTE if config is None else config
        if not somente_login and not cfg.get("envio_liberado"):
            raise ImproperlyConfigured("Envio ao provedor de IA não liberado (IATIVA_AGENTE_ENVIO_LIBERADO).")
        self.cli = resolver_cli(cfg.get("claude_cli", "claude"))
        self.env = ambiente(cfg.get("claude_config_dir", ""))
        self.timeout = int(cfg.get("claude_timeout_s", 120))
        if not 1 <= self.timeout <= 600:
            raise ImproperlyConfigured("Timeout Claude deve estar entre 1 e 600 segundos.")
        self.modelo = cfg.get("claude_modelo", "sonnet")
        self.esforco = cfg.get("claude_esforco", "")

    def _executar(self, args, prompt="", timeout=None):
        return executar_cli([self.cli, *args], prompt, env=self.env, timeout=timeout or self.timeout)

    def checar_cli(self):
        code, help_text, _ = self._executar(["--help"], timeout=10)
        if code or any(flag not in help_text for flag in FLAGS):
            raise FalhaModelo("Atualize o Claude Code: faltam controles obrigatórios de isolamento.", temporaria=True)

    def verificar(self):
        self.checar_cli()
        code, texto, _ = self._executar(["--safe-mode", "auth", "status", "--json"], timeout=10)
        status = _objeto(texto)
        if code or status.get("loggedIn") is not True:
            raise FalhaModelo("Reconecte sua conta em Conectar Claude no IAtiva.", temporaria=True)
        if status.get("authMethod") != "claude.ai":
            raise FalhaModelo("Autenticação não confirmada como assinatura Claude; API e rotas alternativas bloqueadas.", temporaria=True)
        return {"autenticado": True, "metodo": "claude.ai"}

    def _gerar(self, prompt, schema, validar, objetivos, parametros):
        if len(prompt.encode("utf-8")) > 200_000:
            raise FalhaModelo("Contexto do Claude excedeu o limite permitido.")
        self.verificar()
        args = ["--safe-mode", "-p", "--model", self.modelo, *(["--effort", self.esforco] if self.esforco else []),
                "--tools", "", "--disallowedTools", "mcp__*",
                "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                "--no-session-persistence", "--output-format", "json",
                "--json-schema", json.dumps(schema, ensure_ascii=True)]
        code, stdout, stderr = self._executar(args, prompt)
        try:
            result = _objeto(stdout)
        except FalhaModelo:
            if code:
                raise _falha(stderr + stdout) from None
            raise
        if code or result.get("is_error") is True:
            raise _falha(json.dumps(result) + stderr)
        if result.get("type") != "result" or result.get("subtype") != "success":
            raise FalhaModelo("Claude Code não concluiu uma resposta válida.")
        raw = result.get("structured_output")
        if not isinstance(raw, dict):
            raise FalhaModelo("Claude Code não devolveu a saída estruturada solicitada.")
        try:
            validar(raw, objetivos, parametros)
        except (esquema.SaidaInvalida, decisao.DecisaoInvalida):
            raise FalhaModelo("Resposta do Claude fora do contrato permitido.") from None
        return raw

    def interpretar(self, entrada):
        objetivos, parametros = _codigos(entrada)
        return self._gerar(INSTRUCOES + "\n" + _catalogo(entrada) + "\n" + _renderizar(entrada),
                           esquema.esquema_json(objetivos, parametros), esquema.validar, objetivos, parametros)

    def decidir(self, entrada):
        objetivos, parametros = _codigos(entrada)
        return self._gerar(INSTRUCOES_AGENTE + "\n" + _contexto_agente(entrada),
                           decisao.esquema(objetivos, parametros), decisao.validar, objetivos, parametros)
