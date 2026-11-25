## v0.4.12-alpha — Menu visibility and tap fan controls

- Per-menu section visibility and ordering in the existing layout editor, saved independently; reset restores the selected category, cancel discards edits. The layout editor, print actions and safety/recovery access remain fixed. Add Console and Network sections to the catalog.
- Normalize reported Klipper fan duty by configured `max_power` on Print and Control. Send requested percentages without applying the limit a second time; firmware still enforces `off_below` and `max_power`.
- Tap anywhere on fan tracks or drag; commit once on release on both pages, block status-refresh signals, share pending state and command deduplication. Do not send with unavailable/stale printer status. No change to LED or cleaning-lock sliders.

## v0.4.11-alpha — Dashboard multi-tool temperatures

- Remove the two-hotend and four-tile limits for all detected hotends, bed and prioritized chamber. Keep the compact generic-heater fallback, responsive grid and saved tile order.
- No configuration migration, API or printer-control changes.

## v0.4.10-alpha — Per-tool filament sensors

- Read existing Moonraker switch-sensor status and display per-tool presence independently of tool activation and heater state.
- Match unambiguous conventional per-extruder sensor names; support exact `[filament_sensors]` overrides and a single-extruder/single-switch fallback. Do not assign shared or ambiguous sensors by order.
- Distinguish disabled, unavailable, unassigned and motion-only sensors; never infer nozzle loading from motion or a successful macro. Presence means filament at the sensor, not at the nozzle.
- Read-only UI; no macro or runout behavior changes, no extra polling.

## v0.4.9-alpha — Direct tool controls

- Direct tool changes on tile tap (default); optional separate Activate button.
- Independent confirmation toggles for tool changes, extrusion/retract and load/unload (including All).
- Preserve tracked-motion, live-state and active-extruder checks; no motion on programmatic selection refreshes.
- Fix duplicated `bash` in kiosk installation script.
- Remote display host separation is not part of this release.

## v0.4.8-alpha — Multi-tool & filament management

- Detect numbered extruder objects without a four-tool limit, preserve numeric IDs and support explicit `[tools]` and `[tool_commands]` mappings. Distinguish selection from the active extruder reported by Klipper; selecting a tile never moves the printer. Activate through a discovered macro, not raw ACTIVATE_EXTRUDER.
- Show current and target temperatures for every tool, with selected heater state and editable target limited by Klipper configuration. Keep PA and firmware retraction controls. Use two tool columns on tiny displays.
- Resolve existing filament macros automatically or via `[filament_macros]` exact command overrides with optional `{tool}` and `{extruder}` placeholders. All-tool buttons require multiple extruders and an existing all-tool macro. Never generate filament motion from macro names alone. Single-tool actions require the selected tool to be active.
- Use the existing FIFO/guarded motion path, recheck ready/idle status, required macro and active extruder before dispatch, wait for command and M400, and extend macro request timeout to 900 seconds. No automatic retry after uncertain responses. Local tracked operations block manual toolhead/filament actions, normal confirmed G-code submission, file start and host shutdown. Other clients and asynchronous/delayed printer macros are not interlocked.
- 51 dependency-free tests passed; native Qt and connected-printer validation remain pending. U1-specific UNLOAD_ALL macro is not included: current printer backup is required to verify existing unload and parking behavior. No mandatory config migration.

## v0.4.7-alpha — Graphical Exclude Object

- Add a touch-selectable XY bed map to the existing Print / Exclude Object dialog. Draw real Klipper object polygons at equal X/Y scale, with Y increasing upwards, and show selection (blue), current object (green) and excluded objects (red hatch). Selection does not issue a command.
- Use reported XY travel limits and expand to include object geometry. Fit objects zooms to the occupied area. The map is a coordinate view, not an exact physical plate outline. Center-only metadata uses point markers; missing geometry and overlapping tap targets retain the named list fallback. Keep list and map selections synchronized.
- Retain explicit confirmation and fresh printer/job/object checks before exclusion; close the live selection dialog on job change or observed timer rollback. No new dependencies or config migration.
- 41 dependency-free tests passed (including eight new geometry and hit-testing tests). Native Qt, touchscreen and printer tests remain pending.

## v0.4.6-alpha — Print Files and Exclude Object

- Rename the Files navigation label to Print Files (German: Druckdateien), retaining the existing page ID for saved layouts, shortcuts and startup page. Replace the file dropdown with responsive thumbnail cards, six files per page, newest first, with filename, folder, and estimated print time where available. Missing previews remain selectable. Use a single background preview worker with a bounded download size and discard outdated page results.
- Start prints using the Moonraker filename endpoint after explicit confirmation and a fresh ready/idle check. Do not automatically retry unconfirmed requests. File-list errors no longer invalidate unrelated printer status.
- Poll Klipper's optional exclude_object status. Show a live named-object list in Print, mark the current and excluded objects, and require confirmation before sending an exclusion. Reject invalid names, stale or changed job state, and already-excluded objects. Enable during printing or pause only. Requires object-marked G-code and [exclude_object]; no configuration migration. No graphical bed map yet.
- 33 dependency-free tests passed. Native Qt and printer tests pending.

## v0.4.5-alpha — Homing feedback and visible jog steps

- Replace the jog step dropdown with always-visible, exclusive step buttons; start at 50 mm each app launch, including existing custom step lists. The selected step applies to XYZ. Wrap into rows on tiny displays.
- Grey homing buttons until the associated axes are reported homed; blue after live confirmation. Apply to Control and Dashboard. This is Klipper homed status, not electrical motor feedback.
- Animate locally requested homing on the button and in a status line; temporarily block repeated homing and manual jog during the operation. Wait for the command and M400 plus a new status poll. Dashboard and quick home share the guarded dispatch. External homing is reflected in live axis status, but not tracked as a local command.
- Show failures without auto-retry. If the network response is uncertain, require a printer state reset or a verified operator/UI restart before more local toolhead motion. No configuration migration. Native Qt and hardware testing pending.

## v0.4.4-alpha — Unified Toolhead control

- Combine XYZ G-code coordinates, machine coordinates and per-axis homed status with XY/Z jog, step size, Home all/XY/X/Y/Z and Motors Off in the existing reorderable Control jog block. Coordinates are reported commanded positions, not encoder measurements; clear stale values on a fault or connection error.
- Show Z-Tilt and QGL only when their Klipper objects are discovered; require homed XYZ for these calibration actions. Use existing jog direction/speed settings and theme styles. Stack controls on narrow displays.
- Require a fresh ready/idle status before dispatching Control manual movement, and relevant homed axes for jog. Recheck optional calibration availability; use the existing FIFO and never automatically retry timed-out commands. Klipper remains responsible for movement limits. No config migration.
- Native Qt and hardware testing pending.

## v0.4.3-alpha — Compact Print emergency stop

- Use one shared fixed top-right Emergency Stop header on Dashboard and Print; remove the full-width Print stop bar and M112 suffix from the button label.
- Keep mandatory confirmation (No selected by default), direct Moonraker emergency endpoint and recovery behavior unchanged. No config migration. Native Qt/touch testing pending.

## v0.4.2-alpha — Design cleanup and dashboard emergency stop

- Separate structural Qt widgets, labels, cards and intentional readout backgrounds. Consistent theme-derived values, numeric inputs, section titles, selection, disabled buttons and progress bar styling; retain live heating and fault colors.
- Fixed Dashboard emergency stop replaces the Dashboard host shutdown. Dashboard and Print share the same confirmed direct Moonraker emergency endpoint. Host shutdown moves to Settings → Service Control, retaining its independent confirmation and idle-state checks.
- Small white dot cursor with dark outline throughout VeloxScreen, including Qt dialogs; optional hidden or system pointer in Display comfort. No system-wide cursor changes.
- Retain v0.4.1 functionality and existing config keys; no migration required. Native Qt and hardware checks still required.

## v0.4.1-alpha — Live Z and calibration save

- Select live Z increments of 0.005, 0.010, 0.025 or 0.050 mm. Show the live G-code Z offset and use Z−/Z+ during active printing with homed Z.
- Confirm reset to zero on recognized single-tool setups. Reset moves the Z axis; limited to absolute offsets of 0.5 mm or less.
- Offer permanent calibration saving only for an unambiguous single-tool probe or mechanical Z endstop, and only in confirmed idle/complete state. Validate the live offset, calibration method, pending config and advertised G-code commands again before applying the offset and requesting SAVE_CONFIG.
- During a print, Save after print arms a temporary reminder, not an automatic save. Require a second explicit confirmation after successful completion; invalidate on changed offset/job, cancellation or connectivity failure. Never restore a reset offset automatically.
- Eddy scan/tap requires explicit configuration; custom save macros and ambiguous multi-tool setups keep live adjustment only. No direct editing of printer.cfg.
- No automatic retry after a possibly executed save. Check the console/config after a lost response; uncertain saves are blocked until the UI restarts.
- Includes v0.4.0 quality-of-life features. 14 dependency-free logic tests; physical printer and native Qt tests still required.

## v0.4.0-alpha — Quality of life

- Prominent estimated remaining time and finish-at local clock, progress bar, slicer thumbnail, layer count, filament length and optional mass estimate, active tool.
- Pause / Resume / Cancel (confirmed cancel) and live Z adjustment with fresh print-state checks. Emergency stop pinned outside the Print scroller.
- Bounded 15-minute temperature history for the active nozzle, bed and recognized chamber. Added reorderable print-detail, live-Z and temperature-history blocks.
- Configurable horizontal quick action bar, including presets, macro favorites, homing/cooldown (idle only), navigation and cleaning lock.
- Display dim/blank timers, keep-on-print, optional driver-supported backlight/DPMS, startup page and separate font/button sizing. Cleaning lock uses a deliberate swipe, exits when printing starts or a fault occurs.
- Notification center with filters, optional persisted local history (150 entries), event categories, individual popup preferences, print/temperature/filament and fault events. Existing RESPOND popups retained.
- Based on v0.3.18. Config and runtime update paths retained. Hardware and native Qt regression testing still required.

## v0.3.18-alpha — Live heating and preset indicators

- Highlight heaters red while heating toward a nonzero target, green within ±2 °C, amber when above the requested target, neutral when off or unavailable.
- Display explicit target temperatures and heating state on Dashboard, Print and Temperatures, including multi-tool and generic heaters.
- Highlight matching presets from actual Klipper target values, not a button press. Compare applicable heater targets using the same mapping as the preset command (selected nozzle, bed, chamber). Multiple matching presets may be highlighted; zero-only presets are not highlighted.
- External commands and manual target changes update the indicators on the next poll. Invalidate indicators on faults and connection errors; read-only sensors are never marked as heating.
- No new config options or printer control changes. Native Qt hardware testing pending.

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
