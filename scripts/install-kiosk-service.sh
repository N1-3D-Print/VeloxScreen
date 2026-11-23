#!/usr/bin/env bash
# Optional first-time native kiosk integration for Debian 12/ARM64.
# Does not remove KlipperScreen. Run only after reading docs/INSTALL.md.
set -euo pipefail

if [[ "$(id -u)" -eq 0 ]]; then
  echo 'ERROR: Run as your regular user, not root.' >&2
  exit 1
fi
APP="$HOME/veloxscreen_native"
SERVICE=/etc/systemd/system/veloxscreen.service
ASVC="$HOME/printer_data/moonraker.asvc"
STAMP="$(date +%Y%m%d_%H%M%S)"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ ! -f "$APP/main.py" ]]; then
  echo 'ERROR: VeloxScreen is not installed. Run scripts/install.sh first.' >&2
  exit 1
fi
if systemctl is-active --quiet KlipperScreen.service; then
  echo 'ERROR: KlipperScreen is running. Disable it first (do not uninstall it):' >&2
  echo '       sudo systemctl disable --now KlipperScreen.service' >&2
  exit 1
fi
if ! command -v openbox >/dev/null || ! command -v startx >/dev/null; then
  echo 'ERROR: X11/Openbox is missing. Run scripts/install.sh without --skip-deps.' >&2
  exit 1
fi

# Do not overwrite a third-party session. Old VeloxScreen sessions are backed up.
if [[ -f "$HOME/.xinitrc" ]] && ! grep -Eqi 'VeloxScreen|veloxscreen_native|openbox' "$HOME/.xinitrc"; then
  echo 'ERROR: ~/.xinitrc belongs to another session. Refusing to overwrite it.' >&2
  exit 1
fi
mkdir -p "$APP/backups"
if [[ -f "$HOME/.xinitrc" ]]; then
  cp -a "$HOME/.xinitrc" "$APP/backups/xinitrc.$STAMP"
fi

# This starts the X session; the application is a separate systemd service.
cat > "$HOME/.xinitrc" <<'XINIT'
#!/bin/sh
# VeloxScreen X11 session (keep X alive during application restarts)
xset s off
xset -dpms
xset s noblank
exec /usr/bin/openbox --sm-disable
XINIT
chmod +x "$HOME/.xinitrc"

bash bash "$ROOT/scripts/migrate-autostart.sh"

cat > "$APP/run-veloxscreen.sh" <<'RUNNER'
#!/usr/bin/env bash
set -euo pipefail
export DISPLAY=:0
export XAUTHORITY="$HOME/.Xauthority"
export QT_QPA_PLATFORM=xcb
for i in $(seq 1 90); do
  if /usr/bin/xset q >/dev/null 2>&1; then
    exec /usr/bin/python3 -u "$HOME/veloxscreen_native/main.py"
  fi
  sleep 2
done
echo 'VeloxScreen: X11 :0 did not become ready within 180 seconds.' >&2
exit 1
RUNNER
chmod +x "$APP/run-veloxscreen.sh"

if sudo test -f "$SERVICE"; then
  sudo cp -a "$SERVICE" "$APP/backups/veloxscreen.service.$STAMP"
  if ! sudo grep -Eqi 'VeloxScreen|veloxscreen_native' "$SERVICE"; then
    echo 'ERROR: A service with this name already exists and is not recognized.' >&2
    exit 1
  fi
fi
sudo tee "$SERVICE" > /dev/null <<EOF_SERVICE
[Unit]
Description=VeloxScreen Native Touch UI
After=getty@tty1.service
Wants=getty@tty1.service
StartLimitIntervalSec=0

[Service]
Type=simple
User=$(id -un)
WorkingDirectory=$APP
Environment=HOME=$HOME
Environment=DISPLAY=:0
Environment=XAUTHORITY=$HOME/.Xauthority
ExecStart=$APP/run-veloxscreen.sh
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF_SERVICE
sudo systemctl daemon-reload
sudo systemctl enable veloxscreen.service

# Moonraker Service Control allowlist, preserving other services.
mkdir -p "$(dirname "$ASVC")"
touch "$ASVC"
if ! grep -qx 'veloxscreen' "$ASVC"; then
  printf '\nveloxscreen\n' >> "$ASVC"
fi

echo
printf 'Systemd service installed: %s\n' "$SERVICE"
printf 'Moonraker service allowlist: %s\n' "$ASVC"
echo 'IMPORTANT: tty1 must auto-login as your normal user for the X11 kiosk to launch.'
echo 'For first-time installs, follow the optional tty1 autologin instructions in docs/INSTALL.md.'
echo 'Reboot when ready: sudo reboot'
echo 'If Mainsail does not list veloxscreen, restart Moonraker when no print is active.'
