#!/usr/bin/env bash
# Atualiza o ambiente de DESENVOLVIMENTO do IAtiva a partir do GitHub (branch main).
#
# Fluxo: busca a branch -> se houver commits novos, avança (fast-forward) -> constrói a imagem
# iativa-app:<commit> -> roda os testes nela (isolada, sem rede, CPU/memória limitadas) ->
# scripts/implantar.sh troca para ela sem derrubar consultas (azul/verde, M31).
# Falha antes da troca: o código volta e a versão no ar continua atendendo.
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
#   DOCKER                       comando do Docker (padrão: docker; ex.: "sudo docker")
#
# Os logs registram apenas horários, commits e o resultado de cada passo. Não registram
# conteúdo do .env, dados do ERP ou mensagens do chat.
set -Eeuo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BRANCH="${IATIVA_UPDATE_BRANCH:-main}"
LOG_DIR="${IATIVA_UPDATE_LOG_DIR:-$REPO_DIR/var/atualizacao}"
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
    # Só o código volta: o tráfego nunca saiu da versão em uso (implantar.sh só troca no fim, já validado).
    local reason="$1"
    log "ERRO $reason. Código de volta para ${OLD:0:7}; a versão no ar não mudou."
    git reset --quiet --hard "$OLD"
    exit 1
}

git merge --quiet --ff-only "$NEW" || rollback "falha no git merge"

# Imagem imutável do commit, construída e testada com limites (não disputa CPU com as consultas).
IMAGEM="iativa-app:${NEW:0:12}"
nice -n 15 ionice -c3 "${DOCKER_CMD[@]}" build --build-arg "IATIVA_VERSAO=${NEW:0:12}" -t "$IMAGEM" . >> "$RUN_OUTPUT" 2>&1 \
    || rollback "falha ao construir a imagem"
"${DOCKER_CMD[@]}" run --rm --network none --cpus 1.5 --memory 2g "$IMAGEM" pytest -q -p no:cacheprovider \
    >> "$RUN_OUTPUT" 2>&1 || rollback "testes falharam na nova versão"

# A mesma imagem testada é a implantada (azul/verde, com drenagem).
set +e
"$REPO_DIR/scripts/implantar.sh" "$IMAGEM"
codigo=$?
set -e
case "$codigo" in
    0) log "OK atualizado para ${NEW:0:7}." ;;
    3) rollback "migração não aditiva: publicação automática bloqueada" ;;
    *) rollback "implantação falhou (código $codigo; ver var/implantacao/implantacao.log)" ;;
esac
