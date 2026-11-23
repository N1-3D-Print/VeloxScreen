# Installation — Debian 12 / Raspberry Pi 4

**Read this first:** VeloxScreen is an early alpha. Installation may affect your local display session. Keep SSH access available for recovery. Do not change X11/tty1 settings while printing.

## 1. Prerequisites

- Raspberry Pi 4 or compatible ARM64 system running Debian 12 Bookworm.
- Klipper and Moonraker already working locally (default Moonraker address: `http://127.0.0.1:7125`).
- A directly connected display with working Linux touchscreen drivers.
- A normal non-root account with sudo privileges. The scripts use **your current user's home directory** and do not assume a fixed username.
- Network access for installing Debian packages.

Check:

```bash
cat /etc/os-release
uname -m
sudo systemctl status moonraker --no-pager
```

## 2. Download and install the native app

Download the ZIP from the GitHub repository, extract it, then enter the extracted directory. Alternatively `git clone` the repository after its real URL is available.

```bash
bash scripts/install.sh
```

This installs `python3-pyqt5`, `xserver-xorg`, `xinit`, `openbox`, and supporting packages using apt. It then puts the app in `~/veloxscreen_native/`. The configuration file is created at `~/printer_data/config/veloxscreen.conf` if it does not already exist. Existing configs and source files are backed up in `~/veloxscreen_native/backups/`.

For **updating an existing Pi**, you can skip Debian dependencies:

```bash
bash scripts/install.sh --skip-deps
```

## 3. Decide how the local display should start

If VeloxScreen is **already visible on the display and restartable through Mainsail**, stop here. Do **not** run kiosk setup again.

For a **new, dedicated VeloxScreen kiosk**:

1. Stop KlipperScreen without uninstalling it:

   ```bash
   sudo systemctl disable --now KlipperScreen.service
   ```

2. Install the app's X11/systemd integration:

   ```bash
   bash scripts/install-kiosk-service.sh
   ```

   This writes `~/.xinitrc` only when it finds an empty or recognizable VeloxScreen/Openbox session; otherwise it refuses to overwrite third-party configuration. It also writes `veloxscreen.service`, preserves a backup of the old service file, and adds `veloxscreen` to `~/printer_data/moonraker.asvc`.

3. **First-time tty1 automatic login (optional):** The kiosk requires a login on the physical `tty1` console before `~/.bash_profile` can run `startx`. If tty1 auto-login is already configured, do not add another override. Otherwise, **review your current getty configuration first**:

   ```bash
   sudo systemctl cat getty@tty1.service
   ```

   If there is no existing override and you consent to local auto-login, create one using your actual user account:

   ```bash
   sudo mkdir -p /etc/systemd/system/getty@tty1.service.d
   sudo nano /etc/systemd/system/getty@tty1.service.d/veloxscreen-autologin.conf
   ```

   Example file (replace `YOUR_USER`):

   ```ini
   [Service]
   ExecStart=
   ExecStart=-/sbin/agetty --autologin YOUR_USER --noclear %I $TERM
   Type=idle
   ```

   Then:

   ```bash
   sudo systemctl daemon-reload
   sudo reboot
   ```

   **Security note:** automatic console login is convenient for a dedicated touchscreen but grants anyone with physical access a local user session. Skip it on shared or publicly accessible systems.

The installer does **not** remove or replace KlipperScreen and does not reboot automatically.

## 4. Verify

```bash
sudo systemctl status veloxscreen --no-pager
journalctl -u veloxscreen -n 60 --no-pager
bash scripts/status.sh
```

The display app should start fullscreen. Moonraker status and printer features are read locally. Mainsail can manage `veloxscreen` only once the service is allowed in Moonraker and Moonraker has picked up the change (a Moonraker restart may be necessary; do so only when safe).

## 5. Uninstalling or reversing changes

To disable the application **without deleting it**:

```bash
sudo systemctl disable --now veloxscreen.service
```

To restore KlipperScreen, see [SWITCHING.md](SWITCHING.md). The installer does not automatically remove system packages or touch printer firmware. Backups are stored under `~/veloxscreen_native/backups/`.
