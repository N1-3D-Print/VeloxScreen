#!/usr/bin/env bash
set -euo pipefail
echo '=== Screen services ==='
systemctl is-enabled veloxscreen.service 2>/dev/null || true
systemctl is-active veloxscreen.service 2>/dev/null || true
systemctl is-enabled KlipperScreen.service 2>/dev/null || true
systemctl is-active KlipperScreen.service 2>/dev/null || true
echo
echo '=== Display processes ==='
pgrep -af '(Xorg|startx|main.py|KlipperScreen)' || true
echo
echo '=== Configuration ==='
if [[ -f "$HOME/printer_data/config/veloxscreen.conf" ]]; then
  echo "$HOME/printer_data/config/veloxscreen.conf"
else
  echo 'No printer-local VeloxScreen config found.'
fi
echo
echo '=== Recent VeloxScreen log ==='
journalctl -u veloxscreen.service -n 15 --no-pager 2>/dev/null || true
