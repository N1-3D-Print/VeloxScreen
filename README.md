# VeloxScreen

![VeloxScreen](assets/veloxscreen_splash.png)

**A native touchscreen interface for Klipper & Moonraker.**  
Responsive layout for small Raspberry Pi touchscreens (experimental 320 × 240 to 1024 × 600). No browser required.

> [!WARNING]
> **Early Alpha (v0.4.4)** — bugs and incomplete features are expected. Test printer controls carefully. Found an issue? **Please open a GitHub Issue!** Feedback and suggestions are welcome.

## Features

- **Toolhead control:** Mainsail-inspired XYZ G-code and machine coordinate readouts, homed status per axis, XY directional pad, Z jog, step size, Home all/XY/X/Y/Z, and detected Z-Tilt/QGL in one responsive Control block. Manual movement requires a verified idle printer; jog requires the relevant homed axis. Existing per-axis jog settings and layout ordering are retained.

- **Design cleanup:** consistent theme-derived number fields, transparent ordinary labels, unified cards, controls and progress bars. Default small-dot pointer; hidden or standard system cursor selectable in Display comfort.
- **Emergency stop:** a compact red button fixed at the top right of both Dashboard and Print replaces the full-width Print stop bar. Both pages use the same dedicated Moonraker emergency endpoint with mandatory confirmation. Host shutdown remains in Settings.

- **Advanced Print Monitor:** large estimated time remaining and finish-at clock, progress, thumbnails, layer count, filament usage, active tool, Pause/Resume/Cancel, live Z adjustment with optional confirmed permanent calibration save and temperature history. The emergency stop stays pinned.
- **Quick actions:** choose and reorder shortcuts, presets and macro favorites in Settings. Swipe the bar with one finger.
- **Display comfort:** inactivity dim/blank, optional physical backlight/DPMS, keep-on-print, adjustable font/button sizing, startup page and slide-to-unlock cleaning lock.
- **Notification center:** filter print, heating, filament, G-Code and warning events; optional local history and configurable popups.

- **Live heating status:** red when heating to a target, green within 2 °C of the target, amber when cooling to a lower target. Matching temperature presets are highlighted on Dashboard and Temperatures; live heater states are shown on Dashboard, Print and Temperatures. Off or unavailable heaters are not highlighted.

- **Custom layout:** reorder page blocks, navigation, fans, temperature tiles, macros and presets in Settings. Optional drag mode or touch-friendly Up/Down buttons; saves per printer.

- **Host shutdown:** button in Settings → Service Control with independent optional confirmation (enabled by default). Blocked during active/paused prints or unverified status; let hotends cool first. Shuts down the OS via Moonraker, not the printer power supply.
- **Recovery:** Automatically opens on Klipper error, shutdown or disconnection, including faults triggered externally. Shows the original Klipper state message, a persistent status banner and confirmed Firmware Restart, Restart Klipper and Host Reboot actions. Correct the underlying fault first. A stopped print cannot be resumed.


- **Jog settings:** invert X/Y/Z movement buttons separately and choose manual speeds in mm/s (saved to `veloxscreen.conf`).


- **Print view:** opens automatically when printing starts (optional), with live print progress, actual speed, extrusion flow and acceleration limit, M220/M221 override buttons, fan sliders and adjustable live heater temperatures. An always-confirmed emergency stop uses Moonraker's dedicated emergency endpoint.
- **Navigation:** larger, one-finger scrollable sidebar on 800 × 480 and larger displays; compact dropdown navigation on small screens.

- **Network:** Wi-Fi scan, signal, IP address and connection via NetworkManager (experimental, permission-dependent).


- **Dashboard:** live speed, flow, acceleration, M220/M221, temperatures and homing.
- **Printer controls:** tools, heaters, fan sliders, pressure advance and firmware retract.
- **Customization:** unlimited temperature presets, macros, themes and `veloxscreen.conf`.
- **Extras:** Bed Mesh, G-Code console, LED Effects, notifications and animated startup.
- **Languages:** English (default) and German.

## Customize the layout

Open **Settings → Customize layout → Edit layout**. Choose a category, select an item and tap **Up / Down**, or enable **Drag mode** to reorder by dragging. **Save layout** applies changes without restarting; **Reset order** restores the selected category's defaults when saved.

Dashboard, Print, Control, Tools, Temperatures, Macros and Settings support block ordering. Navigation, fans, heater/sensor tiles, macro buttons and temperature presets have shared item orders. Print still shows only `fan` and `fan_generic`; its status, emergency stop and recovery controls stay fixed. The Dashboard emergency stop and printer fault banner also stay fixed. This is responsive ordering, not free-pixel placement or resizing. New items are appended automatically. All settings remain in `veloxscreen.conf`.

## Print view and emergency stop

The **Print** page opens automatically on a new `printing` state. You can switch to any other page without being forced back during the same print. Configure it using:

```ini
[print_view]
auto_open = true
```

**Print temperatures:** view actual and target temperatures for every discovered hotend, bed and generic heater. Tap the target to edit it using the touch keypad. Chamber sensors are read-only; heater editing requires reported limits and live data.

**Emergency Stop:** a compact red button fixed at the top right of both Dashboard and Print always requires confirmation, regardless of normal command confirmation settings. It calls Moonraker `/printer/emergency_stop` directly, not the normal G-Code queue. It immediately puts Klipper into an emergency shutdown state; recovery will be necessary. Do **not** use it for ordinary pause/cancel actions. 

**Print fans:** only `fan` and `fan_generic` objects are shown, with manual speed controls. `temperature_fan`, `controller_fan`, `heater_fan` and other automatic fans are hidden on Print. The general Control page is unchanged.

## Quality of life

Open **Settings → Display comfort**, **Quick actions**, and **Notification preferences** to customize the new features. Time remaining is estimated from slicer metadata and measured progress, not guaranteed; while paused, finish time is deliberately withheld. Layer information and preview depend on the sliced file and Klipper metadata. Optional grams use the configured material density. The display can dim/blank in software even without backlight support. Physical power management is opt-in and driver-dependent. The cleaning lock requires confirmed idle state and unlocks by a deliberate drag; a new print or fault releases it. See [Quality of life guide](docs/QUALITY_OF_LIFE.md).

## Display scaling

- Detects Qt screen resolution automatically on launch.
- Compact top navigation and stacked controls on narrow displays.
- To override font/UI sizing, set `[display]` → `ui_scale = 1.15` in `~/printer_data/config/veloxscreen.conf` and restart the UI.
- **Early alpha:** 2.8-inch / 320×240 displays have tight controls; keypad and complex edit dialogs may need further work. A screen's physical size cannot be detected reliably from resolution alone.

## Screenshots

*UI previews with simulated printer data — not photos of a running printer.*

| Dashboard | Bed Mesh |
|:---:|:---:|
| ![Dashboard demo](screenshots/01_dashboard_motion.png) | ![Bed mesh demo](screenshots/04_bed_mesh.png) |
| **LED Effects** | **Macros** |
| ![LED effects demo](screenshots/05_led_effects.png) | ![Macros demo](screenshots/06_macros.png) |

[View all screenshots](screenshots/README.md)

## Installation (copy & paste)

**Requirements:** Raspberry Pi 4 / ARM64, Debian 12, Klipper + Moonraker and a working touchscreen. SSH access recommended. **Do not install while printing.**

**First installation** — paste this block into your Raspberry Pi SSH terminal:

```bash
cd ~
sudo apt-get update && sudo apt-get install -y git
git clone https://github.com/N1-3D-Print/VeloxScreen.git
cd VeloxScreen
bash scripts/quick-install.sh
```

Answer the prompts: the installer installs dependencies, sets up X11/systemd, **asks before disabling KlipperScreen**, and offers tty1 autologin (physical-access security tradeoff). KlipperScreen is **not deleted**. No automatic reboot.

Once complete and **no print is running**:

```bash
sudo reboot
```

**Update an already working installation** (does not redo X11/autologin):

```bash
cd ~
[ -d VeloxScreen/.git ] || git clone https://github.com/N1-3D-Print/VeloxScreen.git
cd VeloxScreen
git pull --ff-only
bash scripts/install.sh --skip-deps
```

For custom kiosk/console setups see [Advanced installation](docs/INSTALL.md).

## Mainsail / Moonraker updates (optional)

After installing VeloxScreen from a Git clone, enable one-click updates:

```bash
cd ~/VeloxScreen
bash scripts/setup-moonraker-updates.sh
```

Confirm when prompted, then restart Moonraker **when idle**. The setup saves backups of `moonraker.conf`, adds a lower-case `[update_manager veloxscreen]` section matching `veloxscreen.service`, and makes every VeloxScreen service start copy the latest checked Python code. Existing `veloxscreen.conf` and custom logo remain untouched. See [Moonraker updates](docs/MOONRAKER_UPDATES.md).

## Switch between screens (no uninstall)

**Use VeloxScreen:**

```bash
sudo systemctl disable --now KlipperScreen.service
sudo systemctl enable veloxscreen.service
sudo reboot
```

**Use KlipperScreen again:**

```bash
sudo systemctl disable --now veloxscreen.service
sudo systemctl enable KlipperScreen.service
sudo reboot
```

If you used an older VeloxScreen alpha with an unconditional `startx` autostart, run `bash scripts/migrate-autostart.sh` **before switching**. See [Switching guide](docs/SWITCHING.md). Service names may vary on custom installations.

## Configuration & feedback

- **Config:** `~/printer_data/config/veloxscreen.conf` · [Example](veloxscreen.conf.example)
- **Bug reports / ideas:** open a [GitHub Issue](../../issues) with steps, version and logs (remove private data).
- **Troubleshooting:** [Guide](docs/TROUBLESHOOTING.md)
- **License:** [GPL-3.0](LICENSE)

*Independent community software. Not affiliated with the Klipper, KlipperScreen, Mainsail or Moonraker teams.*
