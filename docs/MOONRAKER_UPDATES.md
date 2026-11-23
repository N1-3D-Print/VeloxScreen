# Moonraker Update Manager

For a normal Git checkout, VeloxScreen can appear in **Mainsail → Machine → Update Manager**.

## Automated setup (recommended)

Run on the printer Pi as your regular user (with sudo privileges):

```bash
cd ~/VeloxScreen
bash scripts/setup-moonraker-updates.sh
```

The script asks permission, backs up `~/printer_data/config/moonraker.conf`, corrects any older `[update_manager VeloxScreen]` section, and installs a small systemd hook to synchronize the app's `main.py` from the Git checkout **before the service launches**. It does not delete KlipperScreen or overwrite custom UI settings. It does not reboot or restart Moonraker automatically.

When the printer is idle:

```bash
sudo systemctl restart moonraker
```

## Manual Moonraker configuration

Adjust the absolute `path` to match your Git clone:

```ini
[update_manager veloxscreen]
type: git_repo
channel: dev
path: /home/nico/VeloxScreen
origin: https://github.com/N1-3D-Print/VeloxScreen.git
primary_branch: main
managed_services: veloxscreen
```

**Case matters:** the updater section, `managed_services`, and systemd unit `veloxscreen.service` must all use lower-case `veloxscreen`. Do not use `[update_manager VeloxScreen]` while setting `managed_services: veloxscreen` — Moonraker rejects that combination.

**Important:** a Git update alone updates `~/VeloxScreen/main.py`, not `~/veloxscreen_native/main.py`. To apply the new code on managed service restarts, use the automated setup script above. Alternatively update manually with `bash scripts/install.sh --skip-deps`.

Moonraker does **not** run `install_script` as a post-update installer. Extra OS dependencies or future service changes may still require running the installation script manually.

## Troubleshooting

```bash
systemctl status veloxscreen --no-pager
sudo systemctl cat veloxscreen.service
journalctl -u veloxscreen -n 60 --no-pager
```

Moonraker updates/restarts should be performed only when the printer is idle.
