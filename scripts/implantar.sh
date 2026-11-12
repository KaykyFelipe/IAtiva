#!/usr/bin/env bash
# Implantação azul/verde do IAtiva, sem interromper consultas (M31).
#
#   scripts/implantar.sh <imagem>   ex.: iativa-app:1a2b3c4d5e6f (já construída E testada)
#   scripts/implantar.sh --reverter volta o tráfego para a versão anterior (se o esquema permitir)
#   scripts/implantar.sh --drenar   termina drenagens pendentes (cor antiga que ainda tinha consultas)
#   scripts/implantar.sh --estado   mostra cor ativa, versões e pendências
#
# Fluxo de <imagem>:
#   1. confere as migrações pendentes: só aditivas passam (verificar_migracoes); senão, bloqueia;
#   2. aplica as migrações (serviço migrar, imagem nova) — a cor ativa continua atendendo;
#   3. sobe app e worker da cor nova; espera /saude/pronto/ responder com a versão certa;
#      se não ficar pronta, desliga a cor nova e mantém o tráfego onde está;
#   4. troca o proxy (nginx reload: as conexões em andamento continuam na cor antiga);
#   5. confere pelo proxy que a versão nova responde; se não, volta o tráfego (reversão);
#   6. drena a cor antiga: worker para de pegar tarefas e termina a que tem; o app só para quando
#      não houver consultas em andamento. Se não esvaziar no prazo, fica rodando e a drenagem
#      fica PENDENTE (nada é encerrado à força).
#
# Variáveis: IATIVA_COMPOSE_DIR (pasta do compose.yaml e do .env; padrão: a do repositório),
#   IATIVA_PROJETO (padrão iativa), IATIVA_PORTA (8000), IATIVA_DRENAGEM_MAX_S (900),
#   IATIVA_PERMITIR_INCOMPATIVEL=1 (só manual: aceita migração não aditiva).
# Logs: só horários, cores, versões e resultados. Nada do ERP nem do .env.
set -Eeuo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIR="${IATIVA_COMPOSE_DIR:-$REPO_DIR}"
PROJ="${IATIVA_PROJETO:-iativa}"
PORTA="${IATIVA_PORTA:-8000}"
DRENAGEM_MAX="${IATIVA_DRENAGEM_MAX_S:-900}"
ESTADO_DIR="$DIR/var/implantacao"
ESTADO="$ESTADO_DIR/estado"
LOG="$ESTADO_DIR/implantacao.log"
SAIDA="$ESTADO_DIR/ultima_saida.log"
export IATIVA_PROJETO="$PROJ" IATIVA_PORTA="$PORTA"
mkdir -p "$ESTADO_DIR/proxy"
cd "$DIR"

log() { printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*" | tee -a "$LOG"; }
dc() { docker compose -p "$PROJ" --project-directory "$DIR" -f "$DIR/compose.yaml" "$@"; }
dcq() { dc "$@" >> "$SAIDA" 2>&1; }

exec 8> "$ESTADO_DIR/.lock"
if ! flock -n 8; then log "AVISO outra implantação em andamento; nada feito."; exit 0; fi

# --- estado ------------------------------------------------------------------------------------
COR_ATIVA="" IMAGEM_ATIVA="" COR_ANTERIOR="" IMAGEM_ANTERIOR="" PENDENTE=""
# shellcheck disable=SC1090
[[ -f "$ESTADO" ]] && source "$ESTADO"
salvar_estado() {
    cat > "$ESTADO.tmp" <<EOF
COR_ATIVA="$COR_ATIVA"
IMAGEM_ATIVA="$IMAGEM_ATIVA"
COR_ANTERIOR="$COR_ANTERIOR"
IMAGEM_ANTERIOR="$IMAGEM_ANTERIOR"
PENDENTE="$PENDENTE"
EOF
    mv "$ESTADO.tmp" "$ESTADO"
}
outra() { [[ "$1" == "azul" ]] && echo verde || echo azul; }
imagem_var() { [[ "$1" == "azul" ]] && echo IATIVA_IMAGEM_AZUL || echo IATIVA_IMAGEM_VERDE; }
exportar_imagens() {  # a cor que não está sendo mexida mantém a própria imagem
    [[ -n "$COR_ATIVA" ]] && export "$(imagem_var "$COR_ATIVA")=$IMAGEM_ATIVA"
    [[ -n "$COR_ANTERIOR" && -n "$IMAGEM_ANTERIOR" ]] && export "$(imagem_var "$COR_ANTERIOR")=$IMAGEM_ANTERIOR"
    return 0
}
versao_de() { docker image inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$1" | sed -n 's/^IATIVA_VERSAO=//p'; }

# --- saúde -------------------------------------------------------------------------------------
pronto_interno() {  # cor, versão esperada
    dc exec -T "app-$1" python -c "
import json, sys, urllib.request
r = json.load(urllib.request.urlopen('http://127.0.0.1:8000/saude/pronto/', timeout=5))
sys.exit(0 if r.get('pronto') and r.get('versao') == '$2' else 1)" >> "$SAIDA" 2>&1
}
esperar_pronto() {
    for _ in $(seq 1 60); do pronto_interno "$1" "$2" && return 0; sleep 2; done
    return 1
}
versao_no_proxy() {
    curl -s -o /dev/null -D - --max-time 5 "http://127.0.0.1:$PORTA/saude/pronto/" | tr -d '\r' \
        | sed -n 's/^[Xx]-[Ii][Aa]tiva-[Vv]ersao: //p'
}
ativas_em() {
    dc exec -T "app-$1" python -c "
import json, urllib.request
print(json.load(urllib.request.urlopen('http://127.0.0.1:8000/saude/ativas/', timeout=5))['ativas'])" 2>/dev/null || echo 0
}
rodando() { [[ -n "$(dc ps -q --status running "$1" 2>/dev/null)" ]]; }

apontar_proxy() {  # cor
    printf 'upstream iativa_app { server app-%s:8000; keepalive 8; }\n' "$1" > "$ESTADO_DIR/proxy/ativo.conf.tmp"
    mv "$ESTADO_DIR/proxy/ativo.conf.tmp" "$ESTADO_DIR/proxy/ativo.conf"
    if rodando proxy; then
        dc exec -T proxy nginx -t >> "$SAIDA" 2>&1 && dc exec -T proxy nginx -s reload >> "$SAIDA" 2>&1
    else
        dcq up -d --no-deps proxy
    fi
}

drenar() {  # cor antiga: worker termina o que tem; app só para sem consultas em andamento
    local cor="$1" inicio
    inicio=$(date +%s)
    if rodando "worker-$cor"; then
        # Sem reiniciar sozinho ao sair (restart: unless-stopped reiniciaria após o SIGTERM).
        docker update --restart=no $(dc ps -q "worker-$cor") >> "$SAIDA" 2>&1 || true
        dcq kill -s SIGTERM "worker-$cor"
    fi
    while true; do
        local a w=0
        a=$(ativas_em "$cor")
        rodando "worker-$cor" && w=1
        if [[ "$a" == 0 ]]; then
            rodando "app-$cor" && dcq stop -t 60 "app-$cor"
        fi
        if ! rodando "app-$cor" && [[ "$w" == 0 ]]; then
            log "OK cor $cor drenada e parada."
            PENDENTE=""
            return 0
        fi
        if (( $(date +%s) - inicio > DRENAGEM_MAX )); then
            PENDENTE="drenar:$cor"
            log "AVISO cor $cor ainda tem consultas ($a) ou tarefa no worker ($w) após ${DRENAGEM_MAX}s; continua rodando. Rode --drenar depois."
            return 1
        fi
        sleep 3
    done
}

# Primeira vez: sai do layout antigo (serviços app/worker sem cor, porta 8000 no app).
APP_ANTIGO="" WORKER_ANTIGO=""
parar_layout_antigo() {  # para o app antigo (libera a porta) e avisa o worker antigo; não espera
    APP_ANTIGO="$(docker ps -aq --filter "label=com.docker.compose.project=$PROJ" --filter "label=com.docker.compose.service=app")"
    WORKER_ANTIGO="$(docker ps -aq --filter "label=com.docker.compose.project=$PROJ" --filter "label=com.docker.compose.service=worker")"
    [[ -z "$APP_ANTIGO$WORKER_ANTIGO" ]] && return 0
    log "INFO layout antigo encontrado: trocando para azul/verde (uma vez; o app antigo não sabe drenar)."
    if [[ -n "$WORKER_ANTIGO" ]]; then
        docker update --restart=no $WORKER_ANTIGO >> "$SAIDA" 2>&1 || true
        docker kill -s SIGTERM $WORKER_ANTIGO >> "$SAIDA" 2>&1 || true
    fi
    [[ -n "$APP_ANTIGO" ]] && docker stop -t 30 $APP_ANTIGO >> "$SAIDA" 2>&1 || true
}
esperar_layout_antigo() {  # depois que o proxy já atende: o worker antigo termina a tarefa que tinha
    [[ -z "$APP_ANTIGO$WORKER_ANTIGO" ]] && return 0
    for _ in $(seq 1 400); do
        [[ -z "$WORKER_ANTIGO" || -z "$(docker ps -q --filter "id=$WORKER_ANTIGO")" ]] && break
        sleep 3
    done
    docker rm -f $APP_ANTIGO $WORKER_ANTIGO >> "$SAIDA" 2>&1 || true
}

# --- comandos ----------------------------------------------------------------------------------
case "${1:-}" in
--estado)
    echo "cor_ativa=$COR_ATIVA imagem_ativa=$IMAGEM_ATIVA versao_ativa=$( [[ -n "$IMAGEM_ATIVA" ]] && versao_de "$IMAGEM_ATIVA")"
    echo "cor_anterior=$COR_ANTERIOR imagem_anterior=$IMAGEM_ANTERIOR pendente=${PENDENTE:-nenhuma}"
    echo "versao_no_proxy=$(versao_no_proxy)"
    exit 0 ;;
--drenar)
    exportar_imagens
    [[ "$PENDENTE" == drenar:* ]] || { log "OK nada pendente."; exit 0; }
    drenar "${PENDENTE#drenar:}" || true
    salvar_estado; exit 0 ;;
--reverter)
    [[ -n "$COR_ANTERIOR" && -n "$IMAGEM_ANTERIOR" ]] || { log "ERRO não há versão anterior registrada."; exit 2; }
    exportar_imagens
    : > "$SAIDA"
    v_ant="$(versao_de "$IMAGEM_ANTERIOR")"
    log "INFO revertendo para $IMAGEM_ANTERIOR (cor $COR_ANTERIOR)."
    dcq up -d --no-deps --force-recreate "app-$COR_ANTERIOR" "worker-$COR_ANTERIOR"
    esperar_pronto "$COR_ANTERIOR" "$v_ant" || { log "ERRO a versão anterior não ficou pronta; tráfego mantido."; exit 1; }
    apontar_proxy "$COR_ANTERIOR"
    sairam="$COR_ATIVA"
    COR_ATIVA="$COR_ANTERIOR" IMAGEM_ATIVA_NOVA="$IMAGEM_ANTERIOR"
    COR_ANTERIOR="$sairam" IMAGEM_ANTERIOR="$IMAGEM_ATIVA" IMAGEM_ATIVA="$IMAGEM_ATIVA_NOVA"
    salvar_estado
    log "OK tráfego na versão $v_ant (cor $COR_ATIVA)."
    drenar "$COR_ANTERIOR" || true
    salvar_estado; exit 0 ;;
"" | -*)
    echo "uso: $0 <imagem> | --reverter | --drenar | --estado" >&2; exit 2 ;;
esac

IMAGEM="$1"
docker image inspect "$IMAGEM" > /dev/null 2>&1 || { log "ERRO imagem $IMAGEM não existe."; exit 2; }
VERSAO="$(versao_de "$IMAGEM")"
[[ -n "$VERSAO" && "$VERSAO" != dev ]] || { log "ERRO a imagem não tem versão (IATIVA_VERSAO)."; exit 2; }
[[ "$IMAGEM" == "$IMAGEM_ATIVA" ]] && { log "OK $VERSAO já está no ar."; exit 0; }
if [[ "$PENDENTE" == drenar:* ]]; then
    exportar_imagens
    drenar "${PENDENTE#drenar:}" || { salvar_estado; log "ERRO drenagem anterior pendente; nova implantação adiada."; exit 4; }
    salvar_estado
fi
: > "$SAIDA"
COR_NOVA="$(outra "${COR_ATIVA:-verde}")"
exportar_imagens
export "$(imagem_var "$COR_NOVA")=$IMAGEM" IATIVA_IMAGEM_NOVA="$IMAGEM"
log "INFO implantando $VERSAO na cor $COR_NOVA (ativa: ${COR_ATIVA:-nenhuma})."

dcq up -d --wait db  # migração só com o banco pronto
# 1-2. Migrações: só aditivas, aplicadas com a versão antiga ainda atendendo.
# Sem versão anterior no ar (primeira instalação) não há com quem ser compatível.
if [[ -n "$COR_ATIVA" ]] && ! dc run --rm --no-deps migrar python manage.py verificar_migracoes >> "$SAIDA" 2>&1; then
    if [[ "${IATIVA_PERMITIR_INCOMPATIVEL:-}" != 1 ]]; then
        log "BLOQUEADO migração não aditiva em $VERSAO: publicação automática bloqueada (ver $SAIDA)."
        exit 3
    fi
    log "AVISO migração incompatível aceita manualmente (IATIVA_PERMITIR_INCOMPATIVEL=1)."
fi
dcq run --rm --no-deps migrar || { log "ERRO falha nas migrações; nada trocado."; exit 1; }
dcq run --rm --no-deps migrar sh -c 'test -f "$IATIVA_SYNTHETIC_ERP_PATH" || python manage.py seed_synthetic'

# 3. Cor nova pronta antes de receber tráfego.
dcq up -d --no-deps --force-recreate "app-$COR_NOVA" "worker-$COR_NOVA"
if ! esperar_pronto "$COR_NOVA" "$VERSAO"; then
    dcq stop -t 30 "app-$COR_NOVA" "worker-$COR_NOVA"
    log "ERRO $VERSAO não ficou pronta; cor $COR_NOVA desligada, tráfego mantido em ${COR_ATIVA:-nenhuma}."
    exit 1
fi

# 4-5. Troca e conferência pelo proxy.
parar_layout_antigo
apontar_proxy "$COR_NOVA"
ok=0
for _ in $(seq 1 15); do [[ "$(versao_no_proxy)" == "$VERSAO" ]] && { ok=1; break; }; sleep 2; done
if [[ "$ok" != 1 ]]; then
    if [[ -n "$APP_ANTIGO" ]]; then  # primeira troca falhou: devolve a porta ao layout antigo
        dcq stop proxy
        docker start $APP_ANTIGO >> "$SAIDA" 2>&1 || true
        [[ -n "$WORKER_ANTIGO" ]] && { docker update --restart=unless-stopped $WORKER_ANTIGO; docker start $WORKER_ANTIGO; } >> "$SAIDA" 2>&1
        log "ERRO $VERSAO não respondeu pelo proxy; app antigo religado."
    elif [[ -n "$COR_ATIVA" ]] && rodando "app-$COR_ATIVA"; then
        apontar_proxy "$COR_ATIVA"
        log "ERRO $VERSAO não respondeu pelo proxy; tráfego de volta para $COR_ATIVA."
    else
        log "ERRO $VERSAO não respondeu pelo proxy e não há cor anterior rodando."
    fi
    dcq stop -t 30 "app-$COR_NOVA" "worker-$COR_NOVA"
    exit 1
fi
COR_ANTERIOR="$COR_ATIVA" IMAGEM_ANTERIOR="$IMAGEM_ATIVA"
COR_ATIVA="$COR_NOVA" IMAGEM_ATIVA="$IMAGEM"
salvar_estado
log "OK tráfego na versão $VERSAO (cor $COR_ATIVA)."
esperar_layout_antigo

# 6. Drenagem da cor antiga (fica parada, não removida: é a versão de reversão).
if [[ -n "$COR_ANTERIOR" ]]; then
    drenar "$COR_ANTERIOR" || true
    salvar_estado
fi
exit 0
