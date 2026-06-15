#!/usr/bin/env bash
# Create a desktop shortcut that opens the dashboard in a clean browser window.
# Run as your normal user (NOT sudo):
#
#   ./create-shortcut.sh                       # opens http://localhost:8088
#   ./create-shortcut.sh http://192.168.1.50:8088   # custom URL
#
set -e
URL="${1:-http://localhost:8088}"
APP_DIR="$(cd "$(dirname "$0")" && pwd)"

if   command -v chromium-browser >/dev/null; then EXEC="chromium-browser --app=$URL"
elif command -v chromium         >/dev/null; then EXEC="chromium --app=$URL"
elif command -v firefox          >/dev/null; then EXEC="firefox $URL"
else EXEC="xdg-open $URL"; fi

DESK="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
mkdir -p "$DESK"
LAUNCH="$DESK/Logos-Node.desktop"

cat > "$LAUNCH" <<DESKTOP
[Desktop Entry]
Version=1.0
Type=Application
Name=Logos Node
Comment=Open the Logos node dashboard
Exec=$EXEC
Icon=$APP_DIR/static/logos-node.svg
Terminal=false
Categories=Network;Monitor;
DESKTOP

chmod +x "$LAUNCH"
gio set "$LAUNCH" metadata::trusted true 2>/dev/null || true
echo "Created shortcut: $LAUNCH"
echo "Double-click 'Logos Node' on your desktop to open the dashboard."
