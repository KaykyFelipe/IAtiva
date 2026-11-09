#!/usr/bin/env bash
# Atualiza o ambiente de DESENVOLVIMENTO do IAtiva a partir do GitHub (branch main).
#
# Fluxo: busca a branch -> se houver commits novos, avança (fast-forward) -> reconstrói a imagem
# -> roda os testes -> recria o container -> confere se o chat responde.
# Qualquer falha volta o código e o container para a versão anterior.
#
# Roda no HOST (terminal Ubuntu/WSL), não dentro de container. Pode ser chamado à mão ou pelo
# agendamento instalado por scripts/instalar_agendamento.sh.
#
# Uso:
#   scripts/atualizar.sh            atualiza se houver novidade
#   scripts/atualizar.sh --checar   só informa se há atualização (não altera nada)
#
# Variáveis opcionais:
#   IATIVA_UPDATE_BRANCH         branch acompanhada (padrão: main)
#   IATIVA_UPDATE_LOG_DIR        pasta de logs (padrão: var/atualizacao)
#   IATIVA_UPDATE_HEALTH_TRIES   tentativas da verificação final, a cada 2 s (padrão: 30)
#   DOCKER                       comando do Docker (padrão: docker; ex.: "sudo docker")
#
# Os logs registram apenas horários, commits e o resultado de cada passo. Não registram
# conteúdo do .env, dados do ERP ou mensagens do chat.
set -Eeuo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BRANCH="${IATIVA_UPDATE_BRANCH:-main}"
LOG_DIR="${IATIVA_UPDATE_LOG_DIR:-$REPO_DIR/var/atualizacao}"
HEALTH_TRIES="${IATIVA_UPDATE_HEALTH_TRIES:-30}"
read -r -a DOCKER_CMD <<< "${DOCKER:-docker}"
MODE="${1:-}"

cd "$REPO_DIR"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/atualizacao.log"
RUN_OUTPUT="$LOG_DIR/ultima_execucao.log"

log() { printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*" | tee -a "$LOG_FILE"; }
dc() { "${DOCKER_CMD[@]}" compose "$@" >> "$RUN_OUTPUT" 2>&1; }

# Evita duas atualizações ao mesmo tempo.
exec 9> "$LOG_DIR/.lock"
if ! flock -n 9; then
    log "AVISO outra atualização já está em andamento; nada feito."
    exit 0
fi

current_branch="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$current_branch" != "$BRANCH" ]]; then
    log "ERRO a pasta está na branch '$current_branch', esperada '$BRANCH'. Nada feito."
    exit 2
fi

if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
    log "ERRO há alterações locais em arquivos versionados. Nada feito (não sobrescrevo alterações locais)."
    exit 3
fi

if ! git fetch --quiet origin "$BRANCH"; then
    log "ERRO não foi possível acessar o GitHub (git fetch). Nada feito."
    exit 4
fi

OLD="$(git rev-parse HEAD)"
NEW="$(git rev-parse "origin/$BRANCH")"

if [[ "$OLD" == "$NEW" ]]; then
    [[ "$MODE" == "--checar" ]] && log "OK sem atualização (${OLD:0:7})."
    exit 0
fi

if ! git merge-base --is-ancestor "$OLD" "$NEW"; then
    log "ERRO o histórico remoto não continua o local (${OLD:0:7} -> ${NEW:0:7}). Atualização manual necessária."
    exit 5
fi

if [[ "$MODE" == "--checar" ]]; then
    log "INFO atualização disponível: ${OLD:0:7} -> ${NEW:0:7}."
    exit 0
fi

: > "$RUN_OUTPUT"
log "INFO atualizando ${OLD:0:7} -> ${NEW:0:7}."

rollback() {
    local reason="$1"
    log "ERRO $reason. Voltando para ${OLD:0:7}."
    git reset --quiet --hard "$OLD"
    if dc build app && dc up -d --remove-orphans; then
        log "INFO versão anterior ${OLD:0:7} restaurada. Detalhes da falha: $RUN_OUTPUT"
    else
        log "ERRO falha ao restaurar a versão anterior; verificar manualmente. Detalhes: $RUN_OUTPUT"
    fi
    exit 1
}

git merge --quiet --ff-only "$NEW" || rollback "falha no git merge"

dc build app || rollback "falha ao construir a imagem"
dc run --rm app pytest -q -p no:cacheprovider || rollback "testes falharam na nova versão"
dc up -d --remove-orphans || rollback "falha ao recriar os containers"

healthy=0
for ((i = 1; i <= HEALTH_TRIES; i++)); do
    if dc exec -T app python -c \
        "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/entrar/', timeout=5)"; then
        healthy=1
        break
    fi
    sleep 2
done
[[ "$healthy" == 1 ]] || rollback "o chat não respondeu após a atualização"

log "OK atualizado para ${NEW:0:7}."
