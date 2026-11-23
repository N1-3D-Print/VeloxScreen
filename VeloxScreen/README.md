# VeloxScreen

![VeloxScreen logo / splash artwork](assets/veloxscreen_splash.png)

**An experimental, native touchscreen UI for Klipper / Moonraker.**

> [!WARNING]
> **EARLY ALPHA — BUGS ARE EXPECTED.** This is a hobby/community project under active development, not a production-certified printer controller. Parts of the UI may be incomplete, unreliable or incorrect. It can send real G-Code, change heater targets, move axes and disable motors. **Do not leave a printer unattended because this UI is installed.** Test controls carefully with the printer idle before using them during a print.
>
> **Found a bug? Please tell us!** Open a GitHub Issue with the steps to reproduce, your display/OS details and sanitized logs. Issues, UI feedback, screenshots and pull requests are very welcome. You do not need to be a programmer to report a problem.

VeloxScreen is a **standalone Python + PyQt5 (Qt Widgets) application** for Raspberry Pi touchscreen displays. It talks to **Moonraker over its HTTP API**; it does not replace Klipper or Moonraker. No Chromium/web browser is required.

The project was designed with compact **800 × 480** touchscreens in mind, including 5-inch and 4.3-inch displays. The layouts are evolving; other screen sizes are not fully tested.

> VeloxScreen is independent community software and is **not affiliated with or endorsed by the Klipper, KlipperScreen, Moonraker, or Mainsail projects**. Product names are used to describe compatibility.

## Current features (v0.3.5 alpha)

- Dashboard: current speed, estimated extrusion flow, maximum velocity/acceleration settings, temperature cards, and axis homing.
- M220 **speed override** in ±5% and ±25% steps; M221 **flow override** in ±1% and ±5% steps.
- Automatic discovery of available extruder/heater/sensor/fan objects. Tool commands and physical extruder objects are treated separately where possible.
- Hotend, bed, and optional chamber presets; user-defined presets in `veloxscreen.conf`.
- Manual extrusion, firmware retraction, pressure advance, motion control, fan sliders.
- Macro discovery, visibility filters, **Hide All / Show All** and configurable confirmations.
- LED effect controls for setups with the optional [klipper-led_effect](https://github.com/julianschill/klipper-led_effect) plugin.
- G-Code console with on-screen keyboard, printer file browser, measured bed-mesh heatmap.
- Configurable colors, English interface by default, optional German UI language, G-Code notification popups.
- Native splash image with optional speed-line animation and adjustable duration.
- Systemd service that can appear in **Mainsail → Service Control** after Moonraker allowlisting.

## Compatibility

**Target platform:** Raspberry Pi 4 / ARM64, Debian 12 Bookworm, X11/Openbox, Python 3 + PyQt5. This project has **not** been fully tested across all printer configurations and displays. Users have reported that the LED-effect controls work in a real setup; do not treat that as validation of all features.

**Required:** a working Klipper + Moonraker installation and a local screen/X11 session. Optional features appear only when the corresponding Klipper objects or plugins are present.

## Install and update

### Existing VeloxScreen users

This preserves `~/printer_data/config/veloxscreen.conf`, the custom splash image and an existing systemd service:

```bash
git clone https://github.com/YOUR_ACCOUNT/VeloxScreen.git
cd VeloxScreen
bash scripts/install.sh --skip-deps
```

Replace `YOUR_ACCOUNT` with the actual GitHub owner **after** you publish the repository. If you downloaded this repository as a ZIP instead, extract it and run `bash scripts/install.sh --skip-deps` inside the extracted folder. **Do not run first-time kiosk installation again if your current Mainsail service already works.**

### First-time installation

Follow **[docs/INSTALL.md](docs/INSTALL.md)** before changing any boot/autologin configuration.

```bash
# With this repository downloaded/extracted on the Pi:
bash scripts/install.sh
# Optional native X11 kiosk service, after reading the instructions:
bash scripts/install-kiosk-service.sh
```

This installs the application under `~/veloxscreen_native/` and the example config under `~/printer_data/config/veloxscreen.conf`. Existing user configs are backed up and are not overwritten. First-time display autologin needs a separate opt-in step documented in the installation guide.

## Switch between VeloxScreen and KlipperScreen without uninstalling either

The following commands only stop/disable the corresponding systemd service. **They do not delete KlipperScreen, Klipper or Moonraker.** Reboot after changing because both applications may use the same physical display/X session.

**Use VeloxScreen:**

```bash
sudo systemctl disable --now KlipperScreen.service
sudo systemctl enable veloxscreen.service
sudo reboot
```

**Return to KlipperScreen:**

```bash
sudo systemctl disable --now veloxscreen.service
sudo systemctl enable KlipperScreen.service
sudo reboot
```

**Important:** This switching procedure assumes VeloxScreen's tty1 X11 autostart is **service-gated**. Older VeloxScreen alpha builds used an unconditional `startx` line in `~/.bash_profile`. If you installed one of those versions, run `bash scripts/migrate-autostart.sh` before switching, then review [docs/SWITCHING.md](docs/SWITCHING.md). KlipperScreen setups with custom X11 sessions may require additional manual adjustments.

Optional helper scripts: `bash scripts/switch-to-veloxscreen.sh` and `bash scripts/switch-to-klipperscreen.sh` (they do not reboot for you).

## Configuration

- **User config:** `~/printer_data/config/veloxscreen.conf` (next to `printer.cfg`)
- **Example:** [`veloxscreen.conf.example`](veloxscreen.conf.example)
- **Documentation:** [docs/CONFIGURATION.md](docs/CONFIGURATION.md)
- **Default UI language:** `language = en`

Examples of configuration:

```ini
[temperature_presets]
PLA = nozzle:210, bed:60
Bed only = bed:60
Chamber only = chamber:45

[macro_visibility]
mode = selected
visible = BRUSH_NOZZLE, PARK_NOZZLE
hidden =

[appearance]
language = en
```

These examples are **not** printer-specific recommendations. Ensure your hardware can safely support the configured values.

## Publishing to GitHub

This is a repository-ready package, **not a live GitHub repository**. Follow [docs/PUBLISHING.md](docs/PUBLISHING.md) to create the repository, upload the files and mark the first release as a pre-release.

## Help, feedback and known limitations

- **Report bugs:** open a **GitHub Issue** using the *Bug report* template. Include VeloxScreen version, printer/OS/display, steps to reproduce, expected vs. actual results and sanitized logs. See [CONTRIBUTING.md](CONTRIBUTING.md).
- **Suggest features:** use the *Feature request* issue template.
- **Troubleshoot startup:** [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).
- **Current limitations:** Qt hardware/long-duration testing is incomplete; tool-to-extruder mapping may require printer-specific adjustment; some custom Klipper macros override standard commands; the current file browser and mesh display are not full Mainsail replacements. See [CHANGELOG.md](CHANGELOG.md).

## License

VeloxScreen source is offered under **GNU GPL v3.0** — see [LICENSE](LICENSE). PyQt5, Klipper and any optional plugins have their own licenses. The bundled artwork is for this project; check third-party and trademark requirements before redistribution under another brand.
