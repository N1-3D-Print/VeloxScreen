#!/usr/bin/env bash
# VeloxScreen guided first-time installation for Debian 12 / X11.
# Does NOT remove KlipperScreen or reboot automatically.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$HOME/veloxscreen_native"
AUTOLOGIN_DIR=/etc/systemd/system/getty@tty1.service.d
AUTOLOGIN_FILE="$AUTOLOGIN_DIR/veloxscreen-autologin.conf"

if [[ "$(id -u)" -eq 0 ]]; then
  echo 'ERROR: Run as your normal Pi account (no sudo before bash).' >&2
  exit 1
fi
if [[ ! -t 0 ]]; then
  echo 'ERROR: Run this installer interactively in SSH or a terminal.' >&2
  exit 1
fi
if [[ ! -d "$HOME/printer_data/config" ]]; then
  echo 'ERROR: ~/printer_data/config is missing. Install Klipper/Moonraker first.' >&2
  exit 1
fi
if ! command -v systemctl >/dev/null; then
  echo 'ERROR: systemd is required.' >&2
  exit 1
fi
if [[ ! -e /dev/tty0 ]]; then
  echo 'WARNING: /dev/tty0 is missing. Check that your Pi exposes a Linux virtual console.'
fi

printf '\nVeloxScreen v0.3.7 alpha - guided installer\n'
printf 'User: %s | Repo: %s\n' "$(id -un)" "$ROOT"
echo 'Requires Debian 12/compatible system, a connected touchscreen, Klipper + Moonraker.'
echo 'This will install the app, set up its X11 kiosk and optionally configure tty1 login.'
echo 'KlipperScreen stays installed. No reboot will be performed automatically.'
echo 'WARNING: The UI is alpha software and can issue real printer commands.'
printf '\n'
read -r -p 'Continue with installation? [y/N]: ' ok
[[ "$ok" =~ ^[Yy]$ ]] || { echo 'Cancelled. Nothing installed.'; exit 0; }

# Keep existing working installations untouched: only update application.
if systemctl is-active --quiet veloxscreen.service; then
  echo 'An active VeloxScreen service was found. Updating only; kiosk settings unchanged.'
  bash "$ROOT/scripts/install.sh"
  echo 'Update complete. Existing autologin and display settings were preserved.'
  echo 'Optional Moonraker Update Manager: bash scripts/setup-moonraker-updates.sh'
  exit 0
fi

# Install packages and copy app before touching screen services.
bash "$ROOT/scripts/install.sh"

if systemctl is-active --quiet KlipperScreen.service; then
  echo
  echo 'KlipperScreen is currently running and must be stopped for this display.'
  read -r -p 'Disable KlipperScreen service (without uninstalling it)? [y/N]: ' switch
  if [[ ! "$switch" =~ ^[Yy]$ ]]; then
    echo 'KlipperScreen unchanged. App installed; kiosk setup skipped.'
    exit 0
  fi
  sudo systemctl disable --now KlipperScreen.service
elif systemctl is-enabled --quiet KlipperScreen.service 2>/dev/null; then
  echo
  read -r -p 'Disable KlipperScreen autostart (keep installed)? [y/N]: ' switch
  if [[ ! "$switch" =~ ^[Yy]$ ]]; then
    echo 'KlipperScreen unchanged. App installed; kiosk setup skipped.'
    exit 0
  fi
  sudo systemctl disable KlipperScreen.service
fi

# Establish X11 + service. Includes profile backup and guard against other X sessions.
bash "$ROOT/scripts/install-kiosk-service.sh"

# Respect existing autologin; never replace an unknown override.
if systemctl cat getty@tty1.service 2>/dev/null | grep -Eq 'agetty.*--autologin'; then
  echo 'Existing tty1 automatic login found. Leaving it unchanged.'
else
  echo
  echo 'No tty1 autologin detected; X11 will not start after boot without console login.'
  echo 'WARNING: Autologin permits someone with physical access to use your Pi account.'
  read -r -p "Configure tty1 autologin for $(id -un)? [y/N]: " autologin
  if [[ "$autologin" =~ ^[Yy]$ ]]; then
    if [[ -e "$AUTOLOGIN_FILE" ]]; then
      echo 'ERROR: Existing VeloxScreen autologin override found but is not recognized. Not overwriting.' >&2
      exit 1
    fi
    sudo install -d -m 755 "$AUTOLOGIN_DIR"
    tmp=$(mktemp)
    trap 'rm -f "$tmp"' EXIT
    printf '[Service]\nExecStart=\nExecStart=-/sbin/agetty --autologin %s --noclear %%I $TERM\nType=idle\n' "$(id -un)" > "$tmp"
    sudo install -m 644 "$tmp" "$AUTOLOGIN_FILE"
    sudo systemctl daemon-reload
    echo 'tty1 autologin configured.'
  else
    echo 'Skipped tty1 autologin. You may need to log in locally to start the display.'
  fi
fi

echo
echo '====== VeloxScreen setup complete ======'
echo 'KlipperScreen was NOT uninstalled.'
echo 'Config: ~/printer_data/config/veloxscreen.conf'
echo 'Service: veloxscreen.service'
echo 'Optional Moonraker updates: bash scripts/setup-moonraker-updates.sh'
echo 'Check before reboot: systemctl cat getty@tty1.service | tail -15'
echo 'Reboot only when no print is running: sudo reboot'
echo 'If the display stays blank: journalctl -u veloxscreen -n 60 --no-pager'
