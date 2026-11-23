# Switching between VeloxScreen and KlipperScreen

You can keep **both applications installed** and choose which one starts on the local touchscreen. **Do not run both kiosk applications on the same display/X server at the same time.** Switching is safest with a reboot.

## Confirm service names

```bash
systemctl list-unit-files | grep -Ei 'veloxscreen|klipperscreen'
```

On most setups the services are named `veloxscreen.service` and `KlipperScreen.service`; the latter may be case-sensitive.

## Use VeloxScreen

```bash
sudo systemctl disable --now KlipperScreen.service
sudo systemctl enable veloxscreen.service
sudo reboot
```

This keeps KlipperScreen installed. It only prevents it from starting automatically.

## Go back to KlipperScreen

```bash
sudo systemctl disable --now veloxscreen.service
sudo systemctl enable KlipperScreen.service
sudo reboot
```

VeloxScreen and its configuration stay on disk. You can switch back later.

## Important: old tty1 startx entries

Older alpha installations inserted unconditional `startx` commands into `~/.bash_profile`. Disabling the VeloxScreen service alone would **not** disable this X11 startup, and KlipperScreen may end up competing for the same display.

Run this **from the extracted GitHub repository** before your first switch:

```bash
bash scripts/migrate-autostart.sh
```

The migration script:

- Makes a timestamped backup of your shell profile.
- Removes **only recognized old VeloxScreen autostart blocks**.
- Adds a new block that launches X11 on `tty1` **only if `veloxscreen.service` is enabled**.
- Refuses to modify unknown `startx` setups.

You can inspect the result using:

```bash
cat ~/.bash_profile
cat ~/.xinitrc
```

Expected new profile block:

```bash
# VELOXSCREEN_X11_AUTOSTART_BEGIN
if [ "$(tty 2>/dev/null)" = "/dev/tty1" ] && [ -z "${DISPLAY:-}" ] && \
   /bin/systemctl is-enabled --quiet veloxscreen.service; then
    exec startx -- :0 vt1 -nolisten tcp
fi
# VELOXSCREEN_X11_AUTOSTART_END
```

**Extra caveat:** A custom KlipperScreen service may manage its own X server or require a different virtual terminal. If it does not appear after switching back, check both X processes and the `getty@tty1` auto-login drop-ins before changing further settings:

```bash
pgrep -af 'Xorg|startx|KlipperScreen|main.py'
sudo systemctl cat getty@tty1.service
journalctl -u KlipperScreen -n 50 --no-pager
```

Do not delete `printer.cfg`, `moonraker.conf`, KlipperScreen's installation folder, or any Python virtual environment to switch screens.

## Optional helpers

```bash
bash scripts/switch-to-veloxscreen.sh
# or
bash scripts/switch-to-klipperscreen.sh
```

The scripts only change systemd enablement; **they never reboot automatically**. Review any preexisting custom kiosk startup first.
