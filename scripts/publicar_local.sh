#!/usr/bin/env bash
# M107: coloca no ar, SÓ NESTE SERVIDOR, uma correção validada pela manutenção automática.
#
# Uso: scripts/publicar_local.sh <branch-da-correção>   (ex.: manutencao/inc-12)
#
# Fluxo: leva o commit da correção para a main local de /opt/IAtiva (fast-forward ou cherry-pick, nunca
# push) -> constrói a imagem (cache: só o código muda) -> se a base mudou desde a validação, roda a suíte
# de novo -> scripts/implantar.sh troca a cor sem interromper quem está usando (azul/verde, M31).
# Falha antes da troca: o código volta e a versão no ar continua atendendo.
#
# Depois a TI revisa e envia ao GitHub:  git log origin/main..main   (scripts/atualizar.sh preserva
# esses commits locais rebaseando-os sobre a main do GitHub).
#
# Saída: imprime "PUBLICADO <commit>" quando a versão nova está no ar.
set -Eeuo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BRANCH="${IATIVA_UPDATE_BRANCH:-main}"
LOG_DIR="${IATIVA_UPDATE_LOG_DIR:-$REPO_DIR/var/atualizacao}"
read -r -a DOCKER_CMD <<< "${DOCKER:-docker}"
CORRECAO="${1:?informe a branch da correção}"

cd "$REPO_DIR"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/atualizacao.log"
RUN_OUTPUT="$LOG_DIR/ultima_publicacao_local.log"
log() { printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*" | tee -a "$LOG_FILE" >&2; }

[[ "$CORRECAO" =~ ^manutencao/inc-[0-9]+$ ]] || { log "ERRO branch de correção inválida."; exit 2; }

# Mesma trava do atualizar.sh: nunca duas publicações ao mesmo tempo (espera até 5 min).
exec 9> "$LOG_DIR/.lock"
flock -w 300 9 || { log "ERRO outra atualização em andamento há mais de 5 min; correção não publicada."; exit 4; }

[[ "$(git rev-parse --abbrev-ref HEAD)" == "$BRANCH" ]] || { log "ERRO pasta fora da branch $BRANCH."; exit 2; }
[[ -z "$(git status --porcelain --untracked-files=no)" ]] || { log "ERRO alterações locais não commitadas."; exit 3; }

OLD="$(git rev-parse HEAD)"
rollback() {
    log "ERRO $1. Correção local não publicada; código de volta para ${OLD:0:7}; a versão no ar não mudou."
    git cherry-pick --abort >/dev/null 2>&1 || true
    git reset --quiet --hard "$OLD"
    exit 1
}

: > "$RUN_OUTPUT"
REVALIDAR=0
if git merge --quiet --ff-only "$CORRECAO" >> "$RUN_OUTPUT" 2>&1; then
    :
else
    # A main local andou desde a validação: aplica só o commit da correção por cima e revalida.
    REVALIDAR=1
    git -c user.name="IAtiva Manutenção" -c user.email=manutencao@iativa.invalid \
        cherry-pick "$CORRECAO" >> "$RUN_OUTPUT" 2>&1 || rollback "conflito ao aplicar a correção"
fi
NEW="$(git rev-parse HEAD)"
log "INFO correção local $CORRECAO -> ${NEW:0:7} (sem GitHub)."

IMAGEM="iativa-app:${NEW:0:12}"
"${DOCKER_CMD[@]}" build --build-arg "IATIVA_VERSAO=${NEW:0:12}" -t "$IMAGEM" . >> "$RUN_OUTPUT" 2>&1 \
    || rollback "falha ao construir a imagem"
if [[ "$REVALIDAR" == 1 ]]; then
    "${DOCKER_CMD[@]}" run --rm --network none --cpus 4 --memory 3g "$IMAGEM" pytest -q -p no:cacheprovider -n 4 \
        >> "$RUN_OUTPUT" 2>&1 || rollback "testes falharam com a correção sobre a versão atual"
fi

set +e
"$REPO_DIR/scripts/implantar.sh" "$IMAGEM"
codigo=$?
set -e
case "$codigo" in
    0) log "OK correção local no ar: ${NEW:0:7}."; echo "PUBLICADO $NEW" ;;
    3) rollback "migração não aditiva: publicação bloqueada" ;;
    *) rollback "implantação falhou (código $codigo)" ;;
esac
