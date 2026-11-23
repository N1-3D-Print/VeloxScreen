#!/usr/bin/env bash
# Copy a checked Git repository revision into the runtime on each service start.
# Never overwrite user configuration or customized splash artwork.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$HOME/veloxscreen_native"
SOURCE="$REPO/main.py"
TARGET="$APP/main.py"

if [[ ! -s "$SOURCE" ]]; then
  echo 'VeloxScreen sync: repository source unavailable; keeping current runtime.' >&2
  exit 0
fi
/usr/bin/python3 - "$SOURCE" <<'PY_CHECK'
import ast, sys
from pathlib import Path
ast.parse(Path(sys.argv[1]).read_text(encoding="utf-8"))
PY_CHECK
mkdir -p "$APP/backups" "$APP/assets"
if [[ ! -f "$TARGET" ]] || ! cmp -s "$SOURCE" "$TARGET"; then
  if [[ -f "$TARGET" ]]; then
    cp -a "$TARGET" "$APP/backups/main.py.$(date +%Y%m%d_%H%M%S)"
  fi
  install -m 644 "$SOURCE" "$TARGET.new"
  mv -f "$TARGET.new" "$TARGET"
  echo 'VeloxScreen sync: program updated from Git checkout.'
fi
if [[ ! -s "$APP/assets/veloxscreen_splash.png" && -s "$REPO/assets/veloxscreen_splash.png" ]]; then
  install -m 644 "$REPO/assets/veloxscreen_splash.png" "$APP/assets/veloxscreen_splash.png"
fi
