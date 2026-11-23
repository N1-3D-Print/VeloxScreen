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
