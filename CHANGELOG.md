## v0.3.17-alpha — Custom layout editor

- Reorder Dashboard, Print, Control, Tools, Temperatures, Macros and Settings blocks and the navigation menu.
- Shared ordering of fans, temperature tiles, macro buttons and presets; preserve existing `fan_order` and append newly discovered objects.
- One-finger scrolling in normal edit mode; optional drag mode and Up/Down buttons. Save or cancel, and reset per category.
- Keep emergency stop, print status, recovery shortcut, host shutdown and fault banner pinned; no printer commands are issued by layout changes.
- Persist ordering in `[layout]`; apply to existing UI without a service restart. Dynamic lists update on the next status poll.
- Native Qt drag/touch testing pending.

## v0.3.16-alpha — Manual fans only on Print

- Show only `fan` and `fan_generic` on the Print page, including auxiliary and tool-specific generic fans.
- Hide `temperature_fan`, `heater_fan`, `controller_fan` and all other automatic fan types from Print.
- Preserve existing fan order, speed controls and the general Control page.
- No configuration migration required.

## v0.3.15-alpha — Dashboard host shutdown

- Compact Shutdown Host button at the top of Dashboard, using Moonraker `POST /machine/shutdown`.
- Independent, persistent confirmation setting (`[behavior] confirm_shutdown = true` by default), applied immediately.
- Disable shutdown during printing, paused prints or unknown/unavailable print status; verify idle state again before sending the request.
- Show request feedback; do not retry automatically if Moonraker disconnects. Emergency stop and recovery confirmations are unchanged.
- Shut down only after cooling the hotends. Native touchscreen and real host shutdown testing pending.

## v0.3.14-alpha — Printer fault and shutdown recovery

- Poll Moonraker server and printer status independently of object queries, including during Klipper shutdown.
- Automatically open Recovery for printer errors, shutdown and lost connections; keep a visible status banner on other pages.
- Show original Klipper state message and distinguish reported M112 from other shutdowns.
- Confirm firmware restart, Klipper restart and host reboot separately; show request results. No automatic restart or print resume.
- Native Qt hardware testing pending.

## v0.3.13-alpha — Print temperature controls

- Add live actual and editable target temperatures for every detected hotend, bed and generic heater on Print.
- Display chamber sensors without writable controls.
- Use existing heater keypad and configured maximum temperature; disable target buttons when live data or limits are unavailable.
- Compact single-column layout on small screens. Existing emergency recovery, jog, Wi-Fi and other controls remain unchanged.
- On-device Qt test pending.

## v0.3.12-alpha — Emergency-stop recovery controls

- Automatically open a recovery dialog after the M112 emergency-stop request succeeds.
- Actions: Restart Klipper (`POST /printer/restart`), Firmware Restart (`POST /printer/firmware_restart`), and Reboot Host (`POST /machine/reboot`).
- Recovery menu remains accessible from Print and Settings if dismissed, or after a later UI restart.
- Confirmation required for every recovery action; emergency stop itself retains its own confirmation.
- Recovery requests bypass the queued G-code worker. The host reboot may disconnect the display/network.
- Does not automatically resume a failed print. Moonraker must permit the requested operation.
- Native Qt on-device test pending. Includes v0.3.11 jog settings and all previous features.

## v0.3.11-alpha — Jog settings

- Settings: invert X, Y and Z jogging independently; adjust per-axis manual jog speed in mm/s.
- Settings saved in `[jog]` and applied immediately without restarting.
- Only touchscreen jog commands are affected, not homing or print kinematics.
- Includes all fixes/features from v0.3.10.

## v0.3.10-alpha — One-finger sidebar navigation

- Use the same kinetic `FingerScrollArea` for sidebar navigation and the main content.
- Sidebar swipe now supports single-finger scrolling on touchscreens that emulate mouse drag events.
- Keep large navigation buttons, compact dropdown navigation and all print controls unchanged.
- Requires on-device touchscreen verification.

## v0.3.9-alpha — Print view and larger navigation

- Auto-open print view on transition into `printing`, with optional `[print_view] auto_open = true`.
- Show progress, filename, live speed (mm/s), extrusion flow (mm³/s), current acceleration limit (mm/s²).
- M220 speed and M221 flow override controls stay synced across pages.
- Live fan control for writable fans; heater and temperature fans remain read-only.
- Always-confirmed Emergency Stop button uses Moonraker `/printer/emergency_stop` immediately.
- Larger sidebar navigation with vertical scrolling; compact layouts still use the top dropdown.
- Fix the compact-mode crash (`wrapped C/C++ object of type QLabel has been deleted`) by retaining the hidden sidebar in its parent layout.
- Pre-release, hardware testing pending.

## v0.3.8-alpha — Wi-Fi network page (experimental)

- New Network page: current Wi-Fi interface, local IP, nearby networks, signal level, scan and connect.
- Touch keyboard for WPA-personal credentials, with masked entry; existing saved profiles can be connected without re-entering a password.
- NetworkManager (`nmcli`) integration without sudo or logging passwords in the application configuration.
- Explicit warning before switching the current network. If NetworkManager/PolicyKit denies access, VeloxScreen displays the error.
- Open and WPA-personal networks only; WPA-Enterprise/WPA3-only may need another configuration method.
- Runtime updater now copies both `main.py` and `wifi_manager.py`.
- Not yet tested on Raspberry Pi hardware. Network changes may interrupt remote access.

## v0.3.7-alpha (2026-11-23)

- Bundled the responsive UI from v0.3.6 (experimental 320×240 through larger displays).
- Added optional Moonraker Update Manager setup with correct lower-case service naming.
- Added safe runtime sync on managed service starts, preserving custom config and logo.
- Fixed the kiosk installer invoking `migrate-autostart.sh` without execute permission.
- Updated English installation and troubleshooting documentation.

## v0.3.6-alpha — Responsive touchscreen layouts

- Detect smaller Qt display resolutions and switch sidebar navigation to a top dropdown.
- Stack speed/flow override panels on compact screens and adapt dashboard cards/presets.
- Reduce fixed dialog-window sizes to available screen bounds.
- Add optional `[display] ui_scale = auto` (or 0.7–1.5 for manual scaling).
- No printer commands or Klipper logic changed.
- Experimental: 320×240 complex dialogs and touch targets require device testing.

# Changelog

## v0.3.5-alpha — first public GitHub package

- Repository packaging, English documentation and installation scripts.
- Explicit alpha warning, issue templates and feedback guide.
- Reversible VeloxScreen/KlipperScreen service switching without uninstalling either.
- Maintains native PyQt5 application and existing printer-local config, LED effects, M220/M221 control, macro visibility, notification popups, splash animation and bed mesh heatmap.
- No claim of production readiness. Some printer-specific features require manual adjustment; cross-device testing is incomplete.

This package was assembled from the previous v0.3.5 touchscreen alpha. It is **not** a rewrite of the runtime application and does **not** imply every feature has been tested on real hardware.

### Installation improvements
- New guided one-command setup: `bash scripts/quick-install.sh`.
- Fix permission-denied error in kiosk setup by invoking migration with `bash`.
- Guided optional tty1 autologin; no automatic reboot or KlipperScreen removal.
