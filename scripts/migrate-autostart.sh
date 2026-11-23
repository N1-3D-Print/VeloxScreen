#!/usr/bin/env bash
# Gate the tty1 X11 autostart on veloxscreen.service being enabled.
# This allows users to disable VeloxScreen and re-enable KlipperScreen without deleting either.
set -euo pipefail
if [[ "$(id -u)" -eq 0 ]]; then
  echo 'ERROR: Run as the regular user.' >&2
  exit 1
fi
PROFILE="$HOME/.bash_profile"
if [[ ! -e "$PROFILE" ]]; then PROFILE="$HOME/.profile"; fi
export VELOX_PROFILE="$PROFILE"
/usr/bin/python3 <<'PY'
import datetime, os
from pathlib import Path
p = Path(os.environ['VELOX_PROFILE'])
old = p.read_text(encoding='utf-8') if p.is_file() else ''
marker = '# VELOXSCREEN_X11_AUTOSTART_BEGIN'
start_tags = {
    '# VeloxScreen Native Autostart',
    '# VeloxScreen-native-autostart',
    '# VELOXSCREEN_KIOSK_START',
}
lines = old.splitlines(keepends=True)
out = []
removed = 0
i = 0
while i < len(lines):
    if lines[i].strip() in start_tags:
        # Restrict deletion to old, known, short boot blocks containing startx.
        end = next((j for j in range(i + 1, min(i + 12, len(lines)))
                    if lines[j].strip() == 'fi'), None)
        if end is None or not any('startx' in line for line in lines[i:end+1]):
            raise SystemExit('ERROR: Unrecognized VeloxScreen profile block. Edit manually.')
        removed += 1
        i = end + 1
        continue
    out.append(lines[i]); i += 1
new = ''.join(out)
if marker not in new:
    if 'exec startx' in new:
        raise SystemExit('ERROR: Other startx autostart detected. Not changing your profile. See docs/SWITCHING.md.')
    new = new.rstrip() + '\n\n' + '''# VELOXSCREEN_X11_AUTOSTART_BEGIN
# Start X11 for VeloxScreen only when its systemd service is enabled.
if [ "$(tty 2>/dev/null)" = "/dev/tty1" ] && [ -z "${DISPLAY:-}" ] && \\
   /bin/systemctl is-enabled --quiet veloxscreen.service; then
    exec startx -- :0 vt1 -nolisten tcp
fi
# VELOXSCREEN_X11_AUTOSTART_END
'''
if new == old:
    print('Autostart is already service-gated. No changes made.')
else:
    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    if p.is_file():
        backup = p.with_name(p.name + '.backup.' + timestamp)
        backup.write_text(old, encoding='utf-8')
        print('Backed up profile to', backup)
    p.write_text(new, encoding='utf-8')
    print(f'Updated {p}; removed {removed} legacy VeloxScreen autostart block(s).')
print('X11 on tty1 will now start only when veloxscreen.service is enabled.')
PY
