#!/usr/bin/env bash
# Optional Moonraker Update Manager setup for a source checkout.
# Preserves printer configuration; makes backups; does not restart Moonraker.
set -euo pipefail
if [[ "$(id -u)" -eq 0 ]]; then
  echo 'ERROR: Run as your regular Klipper user, not root.' >&2
  exit 1
fi
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONF="$HOME/printer_data/config/moonraker.conf"
SERVICE=/etc/systemd/system/veloxscreen.service
DROPIN_DIR=/etc/systemd/system/veloxscreen.service.d
DROPIN="$DROPIN_DIR/20-git-runtime-sync.conf"
if [[ ! -d "$ROOT/.git" || ! -s "$ROOT/main.py" ]]; then
  echo 'ERROR: Please run from a complete Git clone of VeloxScreen.' >&2
  exit 1
fi
if [[ ! -f "$CONF" || ! -f "$SERVICE" ]]; then
  echo 'ERROR: moonraker.conf or veloxscreen.service not found; install VeloxScreen first.' >&2
  exit 1
fi
if [[ ! -f "$ROOT/scripts/sync-runtime.sh" ]]; then
  echo 'ERROR: scripts/sync-runtime.sh missing.' >&2
  exit 1
fi
if [[ ! -t 0 ]]; then
  echo 'ERROR: This script asks for confirmation; run from an interactive terminal.' >&2
  exit 1
fi
printf 'Moonraker update setup for %s\n' "$ROOT"
echo 'This backs up moonraker.conf, updates the VeloxScreen updater block,'
echo 'and installs a systemd sync hook for fresh code on service restarts.'
echo 'It will NOT change printer.cfg, user VeloxScreen config or customized logos.'
read -r -p 'Proceed? [y/N]: ' answer
[[ "$answer" =~ ^[Yy]$ ]] || { echo 'Cancelled.'; exit 0; }
export VELOX_MOONRAKER_CONF="$CONF" VELOX_REPO="$ROOT"
/usr/bin/python3 <<'PY_CONFIG'
from pathlib import Path
import datetime,os,re
p=Path(os.environ['VELOX_MOONRAKER_CONF'])
old=p.read_text(encoding='utf-8')
# Remove only previous VeloxScreen updater sections, preserving everything else.
pat=re.compile(r'(?im)^\s*\[update_manager\s+veloxscreen\s*\][^\n]*\n')
heads=list(re.finditer(r'(?m)^\s*\[[^\n]+\]\s*$',old))
ranges=[]
for i,h in enumerate(heads):
    if pat.fullmatch(h.group()+ '\n'):
        end=heads[i+1].start() if i+1<len(heads) else len(old)
        ranges.append((h.start(),end))
for a,b in reversed(ranges): old=old[:a]+old[b:]
repo=os.environ['VELOX_REPO']
block = f"\n[update_manager veloxscreen]\ntype: git_repo\nchannel: dev\npath: {repo}\norigin: https://github.com/N1-3D-Print/VeloxScreen.git\nprimary_branch: main\nmanaged_services: veloxscreen\n"
updated=old.rstrip()+ '\n'+block
if updated!=p.read_text(encoding='utf-8'):
    backup=p.with_name(p.name+'.veloxscreen-backup.'+datetime.datetime.now().strftime('%Y%m%d_%H%M%S'))
    backup.write_text(p.read_text(encoding='utf-8'),encoding='utf-8')
    p.write_text(updated,encoding='utf-8')
    print('Backed up:',backup)
print('Moonraker config updated: [update_manager veloxscreen]')
PY_CONFIG
sudo install -d -m 755 "$DROPIN_DIR"
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT
printf '[Service]\nExecStartPre=/bin/bash %s/scripts/sync-runtime.sh\n' "$ROOT" > "$TMP"
sudo install -m 644 "$TMP" "$DROPIN"
sudo systemctl daemon-reload
echo 'Git runtime sync enabled on future VeloxScreen starts.'
echo 'When idle, restart Moonraker to load the update manager entry:'
echo '  sudo systemctl restart moonraker'
echo 'Then Mainsail -> Machine -> Update Manager can update VeloxScreen.'
echo 'CAUTION: Updates may restart VeloxScreen during a print; update only when idle.'
