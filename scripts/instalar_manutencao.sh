#!/usr/bin/env bash
# Instala o agendamento do executor de manutenção (M32): systemd timer a cada 15 min, com prioridade
# baixa e CPU/memória limitadas (não disputa com as consultas). Cria /etc/iativa/manutencao.env
# DESLIGADO se ainda não existir; quem liga é o administrador, editando esse arquivo.
#
#   sudo scripts/instalar_manutencao.sh            instala (ou atualiza) o agendamento
#   sudo scripts/instalar_manutencao.sh --remover  remove o agendamento (a configuração fica)
set -Eeuo pipefail
[[ "$(id -u)" == 0 ]] || { echo "Execute com sudo." >&2; exit 1; }
UNIT=iativa-manutencao
if [[ "${1:-}" == "--remover" ]]; then
    systemctl disable --now "$UNIT.timer" 2> /dev/null || true
    rm -f "/etc/systemd/system/$UNIT.service" "/etc/systemd/system/$UNIT.timer"
    systemctl daemon-reload
    echo "Agendamento da manutenção removido."
    exit 0
fi
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p /etc/iativa
if [[ ! -f /etc/iativa/manutencao.env ]]; then
    cat > /etc/iativa/manutencao.env <<'EOF'
# Executor de manutenção do IAtiva (docs/MANUTENCAO.md). Só root lê e edita.
MANUTENCAO_ATIVA=0
MANUTENCAO_PUBLICAR=0
MANUTENCAO_MAX_POR_DIA=3
MANUTENCAO_TEMPO_S=1800
# MANUTENCAO_GIT_SSH_KEY=/root/.ssh/iativa_manutencao
# MANUTENCAO_GIT_REMOTO=git@github.com:KaykyFelipe/IAtiva.git
# MANUTENCAO_CLAUDE_DIR=/root/.iativa-manutencao-claude
EOF
fi
chmod 600 /etc/iativa/manutencao.env
cat > "/etc/systemd/system/$UNIT.service" <<EOF
[Unit]
Description=IAtiva - executor de manutenção (um incidente por vez)
After=docker.service

[Service]
Type=oneshot
WorkingDirectory=$REPO_DIR
ExecStart=/usr/bin/python3 $REPO_DIR/scripts/manutencao.py
Nice=15
IOSchedulingClass=idle
CPUQuota=150%
MemoryMax=4G
TimeoutStartSec=2h
EOF
cat > "/etc/systemd/system/$UNIT.timer" <<EOF
[Unit]
Description=IAtiva - manutenção a cada 15 minutos

[Timer]
OnBootSec=10min
OnUnitActiveSec=15min
Persistent=true

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now "$UNIT.timer"
echo "Agendamento instalado. Ligue em /etc/iativa/manutencao.env (MANUTENCAO_ATIVA=1)."
