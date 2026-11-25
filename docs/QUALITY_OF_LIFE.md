# Quality of life — v0.4.0 alpha

## Print monitor

- Emergency Stop stays fixed at the top right, outside the scroll area (v0.4.3). It always requires confirmation and is separate from normal print controls.

- Since v0.4.13, the thumbnail is left of progress and time estimates. Pause/Resume shares one position; Exclude Object sits between it and Cancel. Recovery remains available in Settings and the fault banner. The Z offset and step selector are beside the four Z controls; deferred and permanent save behavior is unchanged.
- **TIME REMAINING** and **FINISH AT · EST.** are at the top of Print. The finish clock uses the host time zone, including the date for jobs finishing on a later day. Estimate blends slicer `estimated_time` with observed print duration and progress. Very early prints without a slicer estimate show a placeholder. Paused prints show PAUSED instead of a fixed finish clock. Speed changes, preheat, tool changes and slicer estimates can affect accuracy.
- Preview comes from Moonraker file metadata. Files without embedded thumbnails have no preview. Layer values are read from `print_stats.info` and metadata; current layer requires appropriate slicer reporting (e.g. SET_PRINT_STATS_INFO). Missing information is not fabricated.
- Filament length is from `print_stats.filament_used`. To enable an approximate mass, enter filament density in Settings > Display comfort or `[print_view] filament_density`. Use only for prints with the same material density and filament diameter on all tools. No per-tool material accounting is implied.
- Pause/Resume/Cancel use Moonraker's print endpoints and rely on the printer's configured pause/resume/cancel macros. Cancel always asks for confirmation. No automatic resume is performed.
- Live Z uses `SET_GCODE_OFFSET Z_ADJUST=… MOVE=1` and requires active printing with a homed Z axis. Select 0.005 / 0.010 / 0.025 / 0.050 mm increments. Reset to zero moves Z and requires explicit confirmation, a recognized single-tool setup and a bounded offset. Verify direction and nozzle clearance. See [Z offset saving](Z_OFFSET.md) for the optional permanent calibration workflow.
- The active nozzle, bed and recognized chamber are plotted in a bounded 15-minute in-memory history. Nozzle curves break when the active tool changes. The acceleration number remains the configured live acceleration limit, not a measured instantaneous acceleration.

## Quick actions

Settings > Quick actions > Choose quick actions. Select up to 12 entries, reorder with Up/Down and save. Swipe horizontally with one finger if they do not all fit. Preheat opens the temperature page, Lights opens LED Effects; individual presets and macro shortcuts can also be selected. Homing and cooldown shortcuts require confirmed idle state and confirmation. Macro and preset actions honor the existing G-code confirmation preference. The bar can be hidden. Safety controls are separate.

## Display comfort

Settings > Display comfort. Default dim/blank timers: 120/300 seconds; 0 disables the corresponding timer. Keep-on-print is enabled by default and includes paused prints. Faults wake the screen. The first touch on a dimmed/blanked screen is consumed, rather than activating a printer control.

Software dim and blank work without special permissions, but do **not** necessarily switch off the panel backlight. Enable optional physical mode to try a writable `/sys/class/backlight` brightness control or X11 DPMS. Driver support and user permissions vary; no root escalation or blanket permission changes are performed. Hardware brightness is available only when a writable backlight is discovered. Physical blanking may not work on all displays. If necessary, disable physical mode via SSH by setting `hardware_idle = false` and restart VeloxScreen. If a driver leaves the panel dark, restore its brightness via the operating system or reconnect/reboot when the printer is idle. For DPMS, `DISPLAY=:0 XAUTHORITY=$HOME/.Xauthority xset dpms force on` may help on compatible X11 displays. Keep SSH access available when testing.

Font and button size multipliers are independent of the existing automatic screen profile. They apply after a VeloxScreen restart; some controls retain safety/geometry minimums. The selected start page takes effect at next launch. Print auto-open still has priority on a new print.

Cleaning lock: available only when Klipper reports idle. Swipe the handle from left to right to unlock; tapping the end of the slider is not enough. A new print, printer fault or stale connection releases the lock. It is not a security/login lock.

## Notification center

Open Notifications or the Alerts shortcut. Filter by category, tap a message to read it, or clear history after confirmation. History includes up to 150 events; optional persistence writes `~/printer_data/config/veloxscreen_notifications.json` locally (not to GitHub). It can contain filenames and G-code response text; avoid including private information in RESPOND messages. Turn off persistence to remove the stored history. Popups have a global toggle and category preferences. Printer faults still show the persistent recovery banner independently of popup preferences.

Notifications derive from observed Moonraker status transitions and G-code responses. This is not a durable server-side event stream: messages during an offline interval may be missed. Filament warnings require a configured and enabled sensor, and are not a substitute for Klipper's runout handling.

## Test before production

Run the new UI on an idle printer first, especially on 320×240 touchscreens. Confirm time estimate, quick bar scrolling, wake gesture, lock/unlock, brightness recovery, and notification filters before using the new print actions. Use a disposable test print to verify Pause/Resume/Cancel and live Z. No actual printer/Qt hardware test is claimed by this release.
