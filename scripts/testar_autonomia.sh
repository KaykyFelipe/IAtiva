#!/usr/bin/env bash
# Teste REAL de ponta a ponta da autonomia (M38), no servidor Linux, em ambiente ISOLADO:
#
#   pedido no chat → ciclo para sem a evidência exigida (sem exceção) → incidente sanitizado →
#   executor de manutenção (worktree, teste de regressão que falha antes e passa depois, suíte,
#   commit, integração e push) → implantação azul/verde → versão conferida → o worker refaz o
#   pedido original → verificação concluída.
#
# Nada do IAtiva em uso é tocado: outra pasta, outro projeto do compose (iativa-teste, porta 18000),
# outro banco, ERP SINTÉTICO, "GitHub" local (repositório bare) e um defeito injetado SÓ na cópia.
# A IA é substituída por roteiros fixos: o provedor do chat lê decisões de um arquivo e o "agente de
# manutenção" é um programa que aplica a correção conhecida. Assim o teste prova o encanamento
# inteiro, de forma repetível, sem gastar assinatura. Ele NÃO prova a qualidade das decisões do
# modelo real nem da correção escrita por IA.
#
# Uso (na pasta do repositório, no servidor):  scripts/testar_autonomia.sh
set -Euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
T="${IATIVA_TESTE_DIR:-/opt/IAtiva-teste-autonomia}"
SRV="$T/servidor"
export IATIVA_PROJETO=iativa-teste IATIVA_PORTA=18000 IATIVA_COMPOSE_DIR="$SRV" IATIVA_DRENAGEM_MAX_S=240
export IATIVA_DB_PORT_HOST=15432 IATIVA_UPDATE_LOG_DIR="$SRV/var/atualizacao"
FALHAS=0
passou() { echo "PASSOU $*"; }
falhou() { echo "FALHOU $*"; FALHAS=$((FALHAS + 1)); }
confere() { if eval "$2"; then passou "$1"; else falhou "$1"; fi; }
dc() { docker compose -p iativa-teste --project-directory "$SRV" -f "$SRV/compose.yaml" "$@"; }
cor() { sed -n 's/^COR_ATIVA="\(.*\)"/\1/p' "$SRV/var/implantacao/estado"; }
py() { dc exec -T "app-$(cor)" python manage.py shell -c "$1" 2>/dev/null | tail -n "${2:-1}"; }
g() { git -c user.name=teste -c user.email=teste@iativa.invalid "$@"; }
IMAGENS=()

limpar() {
    [[ -f "$SRV/compose.yaml" ]] && dc --profile ferramentas down -v --remove-orphans > /dev/null 2>&1
    for i in "${IMAGENS[@]:-}"; do [[ -n "$i" ]] && docker image rm -f "$i" > /dev/null 2>&1; done
    rm -rf "$T"
}
[[ "${IATIVA_TESTE_MANTER:-}" == 1 ]] || trap limpar EXIT
limpar
mkdir -p "$T"

echo "== 1. cópia isolada com um defeito injetado (só aqui)"
g init -q --bare -b main "$T/remoto.git"
g clone -q "$T/remoto.git" "$SRV" 2> /dev/null
(cd "$REPO_DIR" && { git ls-files -z 2> /dev/null || find . -type f -not -path './var/*' -not -path './.git/*' -print0; }     | xargs -0 tar -cf - ) | tar -xf - -C "$SRV"
# Defeito: a ação que verifica a lista inteira deixa de ser executada (sem erro, sem exceção).
python3 - "$SRV/iativa/agent/agente.py" <<'PY'
import sys
p = sys.argv[1]
s = open(p, encoding="utf-8").read()
alvo = '''    if acao == "investigar_lista":
        return _investigar(user, c, d, texto, ficha)
'''
assert alvo in s, "trecho do defeito não encontrado"
open(p, "w", encoding="utf-8").write(s.replace(alvo, "", 1))
PY
(cd "$SRV" && g add -A && g commit -q -m "copia de teste com defeito injetado" && g push -q origin HEAD:main)
DEFEITO="$(cd "$SRV" && git rev-parse --short=12 HEAD)"
aleatorio() { head -c 64 /dev/urandom | base64 -w0 | tr -d '/+='; }
{
    printf '%s=%s\n' DJANGO_SECRET_KEY "$(aleatorio)" IATIVA_DB_PASSWORD "$(aleatorio)"
    printf '%s\n' IATIVA_DB_PORT_HOST=15432 IATIVA_METADADOS_FONTE=sintetico IATIVA_AGENTE_PROVEDOR=roteiro \
        IATIVA_TESTE_IMPLANTACAO=1 IATIVA_APRENDIZADO_AUTOAPROVACAO=1 IATIVA_AGENTE_ESPERA_S=0
} > "$SRV/.env"
(cd "$SRV" && printf '.env\nvar/\n' >> .git/info/exclude)

echo "== 2. versão com defeito no ar (azul/verde isolado)"
docker build -q --build-arg "IATIVA_VERSAO=$DEFEITO" -t "iativa-app:$DEFEITO" "$SRV" > /dev/null || { echo "FALHOU build"; exit 1; }
IMAGENS+=("iativa-app:$DEFEITO")
"$SRV/scripts/implantar.sh" "iativa-app:$DEFEITO" > /dev/null 2>&1
versao() { curl -s -o /dev/null -D - --max-time 5 "http://127.0.0.1:$IATIVA_PORTA/saude/pronto/" | tr -d '\r' | sed -n 's/^[Xx]-[Ii][Aa]tiva-[Vv]ersao: //p'; }
confere "versão com defeito ($DEFEITO) atendendo" '[[ "$(versao)" == "$DEFEITO" ]]'

echo "== 3. pedido no chat: verificar todos os demitidos"
VAR="$(docker volume inspect -f '{{.Mountpoint}}' iativa-teste_iativa_var)"
cat > "$VAR/roteiro.json" <<'JSON'
{"primeiro": {"resposta": "", "acao": "preparar_consulta", "objetivo": "aprendido-demitidos", "alvo": "", "nota": "",
              "confirmado": false, "opcoes": [],
              "parametros": [{"codigo": "filial-analise", "valor": "TODAS"},
                             {"codigo": "periodo-analise", "valor": "01/09/2026..12/11/2026"}]},
 "depois": {"resposta": "Verificando cada pessoa da lista.", "acao": "investigar_lista", "objetivo": "", "alvo": "",
            "nota": "", "confirmado": false, "opcoes": [], "parametros": [],
            "investigacao": {"campos": {"inicio": "RA_ADMISSA", "fim": "RA_DEMISSA", "situacao": "RA_SITFOLH",
                                        "cargo": "RA_CODFUNC", "categoria": "RA_CATFUNC"}, "identidade": "nome"}}}
JSON
chmod 644 "$VAR/roteiro.json"
py "
from io import StringIO
from django.core.management import call_command
from django.contrib.auth import get_user_model
from django.http import QueryDict
from iativa.accounts.models import AccessPolicy, Department, UserProfile
from iativa.agent import aprendizado
from iativa.chat import conversas
call_command('seed_exemplos', stdout=StringIO())
d = Department.objects.get(codigo='financeiro-cobranca')
u = get_user_model().objects.create_user('teste-autonomia')
UserProfile.objects.create(user=u, department=d, funcao='colaborador')
AccessPolicy.objects.create(user=u, department=d, filiais=['01', '02', '03'], pode_consultar=True, pode_administrar=True, pode_aprovar_conhecimento=True)
q = QueryDict(mutable=True); q.update({'nome': 'Demitidos', 'campo_data': 'RA_DEMISSA'})
q.setlist('colunas', ['RA_MAT', 'RA_NOME', 'RA_ADMISSA', 'RA_DEMISSA']); q.setlist('setores', [str(d.pk)])
cm = aprendizado.aprovar(u, aprendizado.salvar_proposta(u, 'SRA', q))
c = conversas.nova_individual(u)
msg, res = conversas.enviar(u, c, 'verifique se todos os demitidos dos ultimos meses tiveram uma matricula nova')
print('codigo', cm.codigo, 'lista', bool(res and res.ok), 'linhas', len(res.linhas) if res else 0)
" > "$T/pedido.txt"
cat "$T/pedido.txt" | sed 's/^/    /'
ESTADO='from iativa.agent.models import Objetivo, Tarefa; from iativa.manutencao.models import Incidente; from iativa.chat.models import Mensagem
o = Objetivo.objects.order_by("id").first(); i = Incidente.objects.order_by("id").first(); t = Tarefa.objects.order_by("-id").first()
print("objetivo", o.situacao if o else "-", o.exigencia if o else "-", "retomado" if o and o.retomado else "nao-retomado", "| incidente", i.categoria if i else "-", i.situacao if i else "-", i.commit[:12] if i else "-", "| tarefa", t.situacao if t else "-", (t.cobertura or {}).get("total") if t else "-", "| msg", "sim" if Mensagem.objects.filter(texto__startswith="Depois da corre").exists() else "nao")'
antes="$(py "$ESTADO")"; echo "    $antes"
confere "a lista (etapa) foi consultada, mas o pedido NÃO foi dado como cumprido" 'grep -q "lista True" "$T/pedido.txt" && [[ "$antes" == "objetivo manutencao verificacao nao-retomado"* ]]'
confere "capacidade ausente SEM exceção virou incidente elegível" '[[ "$antes" == *"| incidente capacidade_ausente aberto"* ]]'
confere "nenhuma tarefa foi criada com o defeito" '[[ "$antes" == *"| tarefa - -"* ]]'
diag="$(dc exec -T "app-$(cor)" python manage.py manutencao exportar 2> /dev/null | tail -1)"
confere "diagnóstico só com códigos (sem texto do pedido nem nomes)" '[[ "$diag" == *"\"trilha\""* && "$diag" != *"demitidos"* && "$diag" != *"SINTETIC"* && "$diag" != *"matricula"* ]]'

echo "== 4. executor de manutenção (reproduz, corrige, valida, publica, implanta)"
cat > "$T/agente.sh" <<'AG'
#!/usr/bin/env bash
# "Agente" de teste: aplica a correção conhecida e escreve o teste de regressão, como a IA faria.
set -e
wt="$1"
python3 - "$wt/iativa/agent/agente.py" <<'PY'
import sys
p = sys.argv[1]
s = open(p, encoding="utf-8").read()
ancora = '''    if acao == "esquecer_memoria":'''
novo = '''    if acao == "investigar_lista":
        return _investigar(user, c, d, texto, ficha)
'''
assert ancora in s and novo not in s
open(p, "w", encoding="utf-8").write(s.replace(ancora, novo + ancora, 1))
PY
cat > "$wt/tests/test_regressao_verificar_lista.py" <<'PY'
"""Regressão: pedido de verificação da lista inteira precisa virar tarefa (incidente do teste isolado)."""
import pytest

from iativa.agent.models import Tarefa
from iativa.agent.provedores import FakeProvider
from iativa.chat import conversas

from .test_objetivos import PEDIDO, _investigar, _listar
from .test_tarefas import base  # noqa: F401

pytestmark = pytest.mark.django_db


def test_verificacao_da_lista_vira_tarefa(base):
    c = conversas.nova_individual(base["u"])
    msg, res = conversas.enviar(base["u"], c, PEDIDO, FakeProvider([_listar(base), _investigar()]))
    assert msg.ficha.get("tipo") == "tarefa" and Tarefa.objects.count() == 1
PY
AG
chmod +x "$T/agente.sh"
printf '%s\n' MANUTENCAO_ATIVA=1 MANUTENCAO_PUBLICAR=1 MANUTENCAO_MAX_POR_DIA=3 MANUTENCAO_TEMPO_S=600 \
    "MANUTENCAO_GIT_SSH_KEY=/dev/null" "MANUTENCAO_GIT_REMOTO=$T/remoto.git" > "$T/manutencao.env"
IATIVA_REPO="$SRV" IATIVA_MANUTENCAO_DIR="$T/trabalho" IATIVA_MANUTENCAO_CONFIG="$T/manutencao.env" \
    IATIVA_MANUTENCAO_TESTE=1 IATIVA_MANUTENCAO_AGENTE_TESTE="$T/agente.sh" \
    python3 "$SRV/scripts/manutencao.py" > "$T/executor.txt" 2>&1
sed 's/^/    /' "$T/executor.txt" | tail -4
NOVO="$(git --git-dir="$T/remoto.git" rev-parse --short=12 main)"
IMAGENS+=("iativa-app:$NOVO")
confere "executor terminou como publicado" 'grep -q "incidente #[0-9]*: publicado" "$T/executor.txt"'
confere "commit da correção no repositório remoto (sem force)" '[[ "$NOVO" != "$DEFEITO" ]] && git --git-dir="$T/remoto.git" log -1 --format=%s main | grep -q "^fix(manutenção): incidente" && git --git-dir="$T/remoto.git" merge-base --is-ancestor "$DEFEITO" main'
confere "o commit só traz a correção e o teste de regressão" '[[ "$(git --git-dir="$T/remoto.git" diff --name-only "$DEFEITO" main | sort | tr "\n" " ")" == "iativa/agent/agente.py tests/test_regressao_verificar_lista.py " ]]'
confere "versão corrigida ($NOVO) atendendo depois da troca azul/verde" '[[ "$(versao)" == "$NOVO" ]]'

echo "== 5. o worker refaz o pedido original e a verificação conclui"
for _ in $(seq 1 40); do
    depois="$(py "$ESTADO")"
    [[ "$depois" == *"| tarefa concluida"* ]] && break
    sleep 3
done
echo "    $depois"
confere "pedido retomado uma vez e cumprido com evidência de verificação" '[[ "$depois" == "objetivo cumprido verificacao retomado"* ]]'
confere "incidente marcado como publicado com o commit" '[[ "$depois" == *"| incidente capacidade_ausente publicado $NOVO"* ]]'
confere "tarefa criada pela retomada concluiu com cobertura da lista inteira" '[[ "$depois" == *"| tarefa concluida 8 "* ]]'
confere "a pessoa recebeu a resposta sem mandar outra mensagem" '[[ "$depois" == *"| msg sim" ]]'

echo "== resultado: $FALHAS falha(s)"
[[ "$FALHAS" == 0 ]]
