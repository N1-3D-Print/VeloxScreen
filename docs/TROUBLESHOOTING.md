# Troubleshooting

Please open a **GitHub Issue** when you hit a reproducible problem. Early alpha issues are expected and reporting them helps prioritize fixes. See the bug report template for the information to include.

## Blank/black touchscreen

First check the service and whether Xorg is running:

```bash
sudo systemctl status veloxscreen --no-pager
pgrep -af 'Xorg|startx|main.py'
journalctl -u veloxscreen -n 80 --no-pager
```

The application needs a running X11 session. Running `python3 main.py` in SSH/TTY without `DISPLAY` typically produces `qt.qpa.xcb: could not connect to display`; this alone does **not** indicate a broken Qt installation.

## VeloxScreen service doesn't start

```bash
sudo systemctl cat veloxscreen
journalctl -u veloxscreen -b -n 100 --no-pager
ls -l ~/veloxscreen_native/main.py
```

A `203/EXEC` status often indicates a missing or invalid executable path in the service file. If your installation moved, check `ExecStart` and `WorkingDirectory` against the actual paths.

## Unexpected language / untranslated text

```bash
grep -A9 '^\[appearance\]' ~/printer_data/config/veloxscreen.conf
```

The default is `language = en`. When reporting an untranslated string, include a screenshot and the exact UI page.

## Touch controls or keyboard do not respond

Report the touchscreen model, resolution, Qt/PyQt version and which widget failed. Check whether one-finger scrolling works on pages with long content.

## Mainsail doesn't show VeloxScreen in Service Control

Verify the systemd service is installed and `veloxscreen` is in the allowlist:

```bash
sudo systemctl status veloxscreen --no-pager
grep -n '^veloxscreen$' ~/printer_data/moonraker.asvc
```

Moonraker may need to reload this allowlist. Schedule a Moonraker restart only when no critical operation is in progress.

## Share diagnostic logs safely

```bash
journalctl -u veloxscreen -b -n 100 --no-pager
```

**Before posting:** remove passwords, API tokens, private IPs/URLs, serial numbers, user addresses and any other sensitive information. Avoid uploading an entire printer configuration unless necessary.
