#!/usr/bin/env bash
# Install the dashboard as a systemd service so it starts on boot and
# restarts on failure - alongside your logos-node service.
#
#   sudo ./install-service.sh
#
set -e

if [ "$(id -u)" -ne 0 ]; then
  echo "Please run with sudo:  sudo ./install-service.sh" >&2
  exit 1
fi

RUN_USER="${SUDO_USER:-$USER}"
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
PY="$(command -v python3)"

echo "Stopping any manually-started instance..."
pkill -f "$APP_DIR/dashboard.py" 2>/dev/null || true
sleep 1

cat > /etc/systemd/system/logos-dashboard.service <<UNIT
[Unit]
Description=Logos Node Dashboard
After=network-online.target logos-node.service
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$APP_DIR
ExecStart=$PY $APP_DIR/dashboard.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now logos-dashboard
sleep 2
echo
systemctl --no-pager --full status logos-dashboard | head -12
echo
echo "Dashboard service installed. It will start automatically on boot."
echo "Open it at  http://<this-device-ip>:8088"
