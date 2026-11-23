#!/usr/bin/env bash
# Reversible service switch. This script does not reboot automatically.
set -euo pipefail
if ! systemctl list-unit-files KlipperScreen.service --no-legend 2>/dev/null | grep -q 'KlipperScreen.service'; then
  echo 'ERROR: KlipperScreen.service is not installed.' >&2
  echo 'Check its exact name with: systemctl list-unit-files | grep -i klipperscreen' >&2
  exit 1
fi
# Old, unconditional tty1 'startx' blocks conflict with KlipperScreen's display.
for profile in "$HOME/.bash_profile" "$HOME/.profile"; do
  if [[ -f "$profile" ]] && grep -Eq '^# (VeloxScreen Native Autostart|VeloxScreen-native-autostart|VELOXSCREEN_KIOSK_START)$' "$profile"; then
    echo 'ERROR: Legacy unconditional X11 autostart detected.' >&2
    echo 'Run: bash scripts/migrate-autostart.sh' >&2
    exit 1
  fi
done
sudo systemctl disable --now veloxscreen.service
sudo systemctl enable KlipperScreen.service
echo 'KlipperScreen enabled; VeloxScreen disabled but NOT uninstalled.'
echo 'Please reboot to switch the local display: sudo reboot'
echo 'If KlipperScreen does not start, check tty1 autologin and any other X session.'
