#!/usr/bin/env bash
# Instala o agendamento (systemd timer) que roda scripts/atualizar.sh periodicamente.
#
# Requer systemd ativo. No WSL, habilite em /etc/wsl.conf:
#   [boot]
#   systemd=true
# e reinicie o WSL (no Windows: wsl --shutdown).
#
# Uso (no terminal Ubuntu, na pasta do projeto):
#   sudo scripts/instalar_agendamento.sh            a cada 5 minutos, como o usuário que chamou o sudo
#   sudo INTERVALO=15min scripts/instalar_agendamento.sh
#   sudo scripts/instalar_agendamento.sh --remover
set -Eeuo pipefail

if [[ "$(id -u)" != 0 ]]; then
    echo "Execute com sudo." >&2
    exit 1
fi
if ! command -v systemctl > /dev/null || [[ "$(systemctl is-system-running 2> /dev/null || true)" == "offline" ]]; then
    echo "systemd não está ativo. No WSL, habilite em /etc/wsl.conf ([boot] systemd=true) e rode 'wsl --shutdown' no Windows." >&2
    exit 1
fi

UNIT=iativa-atualizacao
if [[ "${1:-}" == "--remover" ]]; then
    systemctl disable --now "$UNIT.timer" 2> /dev/null || true
    rm -f "/etc/systemd/system/$UNIT.service" "/etc/systemd/system/$UNIT.timer"
    systemctl daemon-reload
    echo "Agendamento removido."
    exit 0
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="${SUDO_USER:-$(stat -c %U "$REPO_DIR")}"
INTERVALO="${INTERVALO:-5min}"

if ! id -nG "$RUN_USER" | tr ' ' '\n' | grep -qx docker; then
    echo "Aviso: o usuário '$RUN_USER' não está no grupo 'docker'. Adicione com: sudo usermod -aG docker $RUN_USER" >&2
fi

cat > "/etc/systemd/system/$UNIT.service" << EOF
[Unit]
Description=IAtiva - atualização do ambiente de desenvolvimento a partir do GitHub
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=oneshot
User=$RUN_USER
WorkingDirectory=$REPO_DIR
ExecStart=$REPO_DIR/scripts/atualizar.sh
EOF

cat > "/etc/systemd/system/$UNIT.timer" << EOF
[Unit]
Description=IAtiva - verifica atualizações a cada $INTERVALO

[Timer]
OnBootSec=2min
OnUnitActiveSec=$INTERVALO
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now "$UNIT.timer"
echo "Agendamento instalado: a cada $INTERVALO, como '$RUN_USER', em $REPO_DIR."
echo "Ver próximas execuções: systemctl list-timers $UNIT.timer"
echo "Ver log: tail -f $REPO_DIR/var/atualizacao/atualizacao.log"
