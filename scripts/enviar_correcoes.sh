#!/usr/bin/env bash
# M119: envia ao GitHub as correções que a manutenção automática publicou SÓ neste servidor (M107).
#
# Roda no HOST do servidor, em /opt/IAtiva, como root (a chave de push fica em /etc/iativa/manutencao.env,
# legível só pelo root):
#   sudo scripts/enviar_correcoes.sh            mostra as correções e envia
#   sudo scripts/enviar_correcoes.sh --listar   só mostra o que seria enviado (não altera nada)
#
# Do computador da TI:  ssh servidor-bots "sudo /opt/IAtiva/scripts/enviar_correcoes.sh"
#
# Regras:
# - envia só commits que já estão no ar neste servidor (main local à frente de origin/main);
# - se o GitHub tiver commits novos, para e pede para rodar scripts/atualizar.sh antes (ele integra as
#   correções locais por cima, testa e implanta); assim nunca se envia código diferente do que está no ar;
# - push normal (nunca force). Nada é implantado: o código no ar não muda.
set -Eeuo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BRANCH="${IATIVA_UPDATE_BRANCH:-main}"
CONFIG="${IATIVA_MANUTENCAO_CONFIG:-/etc/iativa/manutencao.env}"
LOG_DIR="${IATIVA_UPDATE_LOG_DIR:-$REPO_DIR/var/atualizacao}"
MODO="${1:-}"

cd "$REPO_DIR"
mkdir -p "$LOG_DIR"
log() { printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*" | tee -a "$LOG_DIR/atualizacao.log"; }

# Mesma trava do atualizar.sh e do publicar_local.sh.
exec 9> "$LOG_DIR/.lock"
flock -w 300 9 || { log "ERRO outra atualização em andamento há mais de 5 min; nada enviado."; exit 4; }

[[ "$(git rev-parse --abbrev-ref HEAD)" == "$BRANCH" ]] || { log "ERRO pasta fora da branch $BRANCH."; exit 2; }
[[ -z "$(git status --porcelain --untracked-files=no)" ]] || { log "ERRO alterações locais não commitadas."; exit 3; }

git fetch --quiet origin "$BRANCH" || { log "ERRO não consegui acessar o GitHub (git fetch)."; exit 4; }
if ! git merge-base --is-ancestor "origin/$BRANCH" HEAD; then
    log "ERRO o GitHub tem commits novos. Rode scripts/atualizar.sh primeiro (integra e implanta) e tente de novo."
    exit 5
fi

N="$(git rev-list --count "origin/$BRANCH..HEAD")"
if [[ "$N" == 0 ]]; then
    echo "Nenhuma correção local para enviar."
    exit 0
fi
echo "Correções no ar só neste servidor ($N):"
git log --format='  %h %s' "origin/$BRANCH..HEAD"
git diff --stat "origin/$BRANCH..HEAD" | tail -1
[[ "$MODO" == "--listar" ]] && exit 0

REMOTO="$(sed -n 's/^MANUTENCAO_GIT_REMOTO=//p' "$CONFIG" 2>/dev/null | tail -1)"
REMOTO="${REMOTO:-git@github.com:KaykyFelipe/IAtiva.git}"
CHAVE="$(sed -n 's/^MANUTENCAO_GIT_SSH_KEY=//p' "$CONFIG" 2>/dev/null | tail -1)"
[[ -n "$CHAVE" && -r "$CHAVE" ]] || { log "ERRO sem chave de envio (MANUTENCAO_GIT_SSH_KEY em $CONFIG); rode com sudo."; exit 6; }

export GIT_SSH_COMMAND="ssh -i $CHAVE -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=accept-new"
if ! git push --quiet "$REMOTO" "HEAD:$BRANCH"; then
    log "ERRO o GitHub recusou o envio (main mudou?). Rode scripts/atualizar.sh e tente de novo."
    exit 7
fi
git fetch --quiet origin "$BRANCH"
log "OK $N correção(ões) local(is) enviada(s) ao GitHub ($(git rev-parse --short HEAD))."
