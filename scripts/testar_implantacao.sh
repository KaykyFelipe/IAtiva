#!/usr/bin/env bash
# Teste REAL de implantação sem interrupção (M31), no servidor Linux, com processos e requisições
# reais, num projeto separado (iativa-teste, porta 18000, banco próprio, ERP SINTÉTICO).
# Não toca no IAtiva em uso: outra pasta, outro projeto do compose, outros volumes e outra porta.
#
# Uso (na pasta do repositório, no servidor):  scripts/testar_implantacao.sh
# Saída: uma linha PASSOU/FALHOU por verificação; código 0 só se todas passarem.
set -Euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TESTE_DIR="${IATIVA_TESTE_DIR:-/opt/IAtiva-teste-implantacao}"
export IATIVA_PROJETO=iativa-teste IATIVA_PORTA=18000 IATIVA_COMPOSE_DIR="$TESTE_DIR" IATIVA_DRENAGEM_MAX_S=180
URL="http://127.0.0.1:$IATIVA_PORTA"
FALHAS=0
passou() { echo "PASSOU $*"; }
falhou() { echo "FALHOU $*"; FALHAS=$((FALHAS + 1)); }
confere() { if eval "$2"; then passou "$1"; else falhou "$1"; fi; }
dc() { docker compose -p iativa-teste --project-directory "$TESTE_DIR" -f "$TESTE_DIR/compose.yaml" "$@"; }
versao() { curl -s -o /dev/null -D - --max-time 5 "$URL/saude/pronto/" | tr -d '\r' | sed -n 's/^[Xx]-[Ii][Aa]tiva-[Vv]ersao: //p'; }
implantar() {
    local c=0
    "$REPO_DIR/scripts/implantar.sh" "$@" > /dev/null 2>&1 || c=$?
    tail -n 3 "$TESTE_DIR/var/implantacao/implantacao.log" 2>/dev/null | sed 's/^/    log: /'
    return "$c"
}

limpar() {
    dc --profile ferramentas down -v --remove-orphans > /dev/null 2>&1 || true
    docker image rm -f iativa-teste:a iativa-teste:b iativa-teste:c iativa-teste:d > /dev/null 2>&1 || true
    rm -rf "$TESTE_DIR"
}
# IATIVA_TESTE_MANTER=1 deixa tudo de pé para investigar uma falha.
[[ "${IATIVA_TESTE_MANTER:-}" == 1 ]] || trap limpar EXIT
limpar
mkdir -p "$TESTE_DIR/deploy" "$TESTE_DIR/var"
cp "$REPO_DIR/compose.yaml" "$TESTE_DIR/"
cp "$REPO_DIR/deploy/nginx.conf" "$TESTE_DIR/deploy/"
aleatorio() { head -c 64 /dev/urandom | base64 -w0 | tr -d '/+='; }
{   # valores gerados agora, só para este teste; a pasta é apagada no fim
    printf '%s=%s
' DJANGO_SECRET_KEY "$(aleatorio)" IATIVA_DB_PASSWORD "$(aleatorio)"
    printf '%s
' IATIVA_DB_PORT_HOST=15432 IATIVA_METADADOS_FONTE=sintetico IATIVA_AGENTE_PROVEDOR=teste         IATIVA_TESTE_IMPLANTACAO=1
} > "$TESTE_DIR/.env"
export IATIVA_DB_PORT_HOST=15432

echo "== imagens de teste"
docker build -q --build-arg IATIVA_VERSAO=teste-a -t iativa-teste:a "$REPO_DIR" > /dev/null || { echo "FALHOU build"; exit 1; }
cid=$(docker create iativa-teste:a)
docker commit --change 'ENV IATIVA_VERSAO=teste-b' "$cid" iativa-teste:b > /dev/null
docker commit --change 'ENV IATIVA_VERSAO=teste-c' --change 'ENV IATIVA_FORCAR_NAO_PRONTO=1' "$cid" iativa-teste:c > /dev/null
docker rm "$cid" > /dev/null
ultima=$(basename "$(ls "$REPO_DIR"/iativa/agent/migrations/0*.py | sort | tail -1)" .py)
cat > "$TESTE_DIR/Dockerfile.d" <<EOF
FROM iativa-teste:a
ENV IATIVA_VERSAO=teste-d
USER root
RUN printf 'from django.db import migrations\n\n\nclass Migration(migrations.Migration):\n    dependencies = [("iativa_agent", "$ultima")]\n    operations = [migrations.RemoveField("tarefa", "reconstruir")]\n' > /app/iativa/agent/migrations/0999_teste_remocao.py
USER iativa
EOF
docker build -q -f "$TESTE_DIR/Dockerfile.d" -t iativa-teste:d "$TESTE_DIR" > /dev/null

echo "== 1. primeira implantação"
implantar iativa-teste:a; confere "versão A no ar" '[[ "$(versao)" == teste-a ]]'

echo "== 2. atualização com consulta longa e tarefa do worker em andamento"
COR_A=$(sed -n 's/^COR_ATIVA="\(.*\)"/\1/p' "$TESTE_DIR/var/implantacao/estado")
dc exec -T "app-$COR_A" python manage.py shell -c "
from django.contrib.auth import get_user_model
from iativa.accounts.models import Department
from iativa.chat.models import ConversationContext
from iativa.agent.models import Tarefa
d, _ = Department.objects.get_or_create(codigo='teste', defaults={'nome': 'Teste'})
u, _ = get_user_model().objects.get_or_create(username='teste-implantacao')
c = ConversationContext.objects.create(tipo='individual', usuario=u, setor=d)
Tarefa.objects.create(usuario=u, conversa=c, tipo='teste_espera', objetivo='teste', criterio_conclusao='teste', plano={'s': 35})
" > /dev/null 2>&1
sleep 4  # o worker A pega a tarefa
curl -s -D "$TESTE_DIR/longa.h" -o "$TESTE_DIR/longa.b" --max-time 120 "$URL/saude/lenta/?s=30" &
pid_longa=$!
sleep 2
( while true; do curl -s -o /dev/null -w '%{http_code} ' --max-time 10 "$URL/saude/ativas/"; versao; sleep 1; done ) \
    > "$TESTE_DIR/amostras.txt" 2>/dev/null &
pid_amostras=$!
implantar iativa-teste:b; cod_b=$?
wait "$pid_longa"
kill "$pid_amostras" 2>/dev/null
confere "implantação B terminou" '[[ $cod_b == 0 ]]'
confere "consulta longa iniciada em A terminou com 200 em A" \
    'grep -q "^HTTP/1.1 200" "$TESTE_DIR/longa.h" && grep -qi "x-iativa-versao: teste-a" "$TESTE_DIR/longa.h"'
confere "novas consultas foram para B" '[[ "$(versao)" == teste-b ]] && grep -q "teste-b" "$TESTE_DIR/amostras.txt"'
confere "nenhuma requisição falhou durante a troca" '! grep -Eq "(^| )(000|5[0-9][0-9]) " "$TESTE_DIR/amostras.txt"'
resultado=$(dc exec -T app-$(sed -n 's/^COR_ATIVA="\(.*\)"/\1/p' "$TESTE_DIR/var/implantacao/estado") python manage.py shell -c "
from iativa.agent.models import Tarefa
t = Tarefa.objects.get(tipo='teste_espera'); print(t.situacao, t.execucoes, t.progresso)" 2>/dev/null | tail -1)
confere "tarefa do worker A concluída por A, sem reprocessar ($resultado)" '[[ "$resultado" == "concluida 1 feita por teste-a" ]]'

echo "== 3. falha de prontidão impede a troca"
implantar iativa-teste:c; cod_c=$?
confere "implantação C recusada" '[[ $cod_c != 0 ]]'
confere "tráfego continua em B" '[[ "$(versao)" == teste-b ]]'

echo "== 4. migração incompatível bloqueia a publicação automática"
implantar iativa-teste:d; cod_d=$?
confere "implantação D bloqueada (código 3)" '[[ $cod_d == 3 ]]'
confere "tráfego continua em B" '[[ "$(versao)" == teste-b ]]'

echo "== 5. reversão depois da troca"
implantar --reverter; cod_r=$?
confere "reversão terminou" '[[ $cod_r == 0 ]]'
confere "tráfego voltou para A" '[[ "$(versao)" == teste-a ]]'

echo "== resultado: $FALHAS falha(s)"
[[ "$FALHAS" == 0 ]]
