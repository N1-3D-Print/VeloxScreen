#!/usr/bin/env bash
# Reversible service switch. This script does not reboot automatically.
set -euo pipefail
if ! systemctl list-unit-files veloxscreen.service --no-legend 2>/dev/null | grep -q 'veloxscreen.service'; then
  echo 'ERROR: veloxscreen.service is not installed.' >&2
  exit 1
fi
if systemctl list-unit-files KlipperScreen.service --no-legend 2>/dev/null | grep -q 'KlipperScreen.service'; then
  sudo systemctl disable --now KlipperScreen.service
fi
sudo systemctl enable veloxscreen.service
echo 'VeloxScreen enabled; KlipperScreen disabled but NOT uninstalled.'
echo 'Please reboot to switch the local display: sudo reboot'
