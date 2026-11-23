#!/usr/bin/env bash
# Install or update VeloxScreen without changing existing service or boot configuration.
set -euo pipefail

if [[ "$(id -u)" -eq 0 ]]; then
  echo 'ERROR: Run as your normal Raspberry Pi user, not with sudo.' >&2
  exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$HOME/veloxscreen_native"
CFG="$HOME/printer_data/config/veloxscreen.conf"
STAMP="$(date +%Y%m%d_%H%M%S)"

if [[ ! -s "$ROOT/main.py" || ! -s "$ROOT/assets/veloxscreen_splash.png" ]]; then
  echo 'ERROR: Source code or default logo is missing.' >&2
  exit 1
fi

if [[ "${1:-}" != '--skip-deps' ]]; then
  echo 'Installing Debian packages needed for the native Qt/X11 application...'
  sudo apt-get update
  sudo apt-get install -y python3-pyqt5 xserver-xorg xinit x11-xserver-utils openbox fonts-dejavu-core
fi

/usr/bin/python3 - "$ROOT/main.py" <<'PY_CHECK'
import ast, pathlib, sys
ast.parse(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))
PY_CHECK
mkdir -p "$APP/backups" "$APP/assets" "$(dirname "$CFG")"

if [[ -f "$APP/main.py" ]]; then
  cp -a "$APP/main.py" "$APP/backups/main.py.$STAMP"
fi
if [[ -f "$CFG" ]]; then
  cp -a "$CFG" "$APP/backups/veloxscreen.conf.$STAMP"
elif [[ -f "$APP/veloxscreen.conf" ]]; then
  cp -a "$APP/veloxscreen.conf" "$CFG"
else
  install -m 644 "$ROOT/veloxscreen.conf.example" "$CFG"
fi

if [[ "$(realpath "$ROOT/main.py")" != "$(realpath -m "$APP/main.py")" ]]; then
  install -m 644 "$ROOT/main.py" "$APP/main.py"
fi
# Preserve a previously customized splash image.
if [[ ! -s "$APP/assets/veloxscreen_splash.png" ]]; then
  install -m 644 "$ROOT/assets/veloxscreen_splash.png" "$APP/assets/veloxscreen_splash.png"
fi

/usr/bin/python3 -m py_compile "$APP/main.py"
if systemctl is-active --quiet veloxscreen.service 2>/dev/null; then
  echo 'Restarting the existing VeloxScreen service...'
  sudo systemctl restart veloxscreen.service
fi
printf 'Application: %s\nConfiguration: %s\n' "$APP" "$CFG"
echo 'Install/update complete. Existing services and KlipperScreen were not modified.'
echo 'New installations: read docs/INSTALL.md before setting up the kiosk service.'
