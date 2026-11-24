# Live Z offset and permanent saving — v0.4.1 alpha

## During a print

Open Print > Live Z offset. Select the step (0.005, 0.010, 0.025 or 0.050 mm), then use Z−/Z+. The number is the entire live `gcode_move.homing_origin.z`, not a separately measured nozzle gap or per-tool calibration. Verify the direction and nozzle clearance. Reset to zero is a deliberate motion and always requires confirmation. It is disabled on recognized multi-tool printers and offsets outside ±0.5 mm.

## Save after print

1. When the desired offset is visible, tap Save after print. This only arms an in-memory reminder for that job and exact live offset.
2. When Klipper reports a successfully completed, inactive print and the offset still matches, a notification appears. Open Print and tap Confirm & save Z offset.
3. Verify the displayed offset and calibration method. Confirm the dialog only if this is the intended permanent calibration correction. Do not start another print or send other calibration commands concurrently.
4. Klipper applies the live offset and requests `SAVE_CONFIG`. This can rewrite the printer configuration and restart Klipper. Verify the console, saved configuration and first layer after restart. The display does not guarantee persistence merely from a successful HTTP request.

If PRINT_END resets the live offset, the job is cancelled, the connection fails or the UI restarts, the reminder is discarded. The app does not restore an old offset or restart Klipper automatically. You can also save a current nonzero offset directly when the printer is in standby/complete. An offset outside ±0.5 mm, unknown print state or other pending SAVE_CONFIG changes blocks saving. The limit is a UI guardrail, not proof of mechanical safety.

## Supported methods

- Mechanical `[stepper_z]` endstop, when unambiguous: `Z_OFFSET_APPLY_ENDSTOP`.
- Standard `[probe]`, `[bltouch]`, `[smart_effector]` or registered `[cartographer]` using `probe:z_virtual_endstop`: `Z_OFFSET_APPLY_PROBE`, subject to the installed firmware advertising that command. Verify the applicable Cartographer calibration mode first.
- A single standard Eddy probe requires explicit `[babystep] eddy_method = scan` or `tap`. Scan uses `Z_OFFSET_APPLY_PROBE`; tap uses `Z_OFFSET_APPLY_PROBE METHOD=tap` only on firmware supporting that parameter. Older vendor macros, custom homing configurations that cannot be resolved, and custom save-command overrides are intentionally not guessed. Default is no Eddy save.
- IDEX/multi-tool, nonstandard toolchangers, multiple ambiguous probes, unrecognized kinematics or custom save macros: live adjustment remains available, but permanent global calibration saving is disabled. Use the printer's tool-specific calibration workflow. Detection is conservative, not exhaustive; do not use global saving to store individual tool offsets or material-specific offsets.

The UI checks current print state, virtual SD activity, pause status, live offset, configured calibration method, pending SAVE_CONFIG state and available commands immediately before submission. Other clients are not locked out, so do not initiate any concurrent operation. If a save request times out, inspect the console and printer.cfg before restarting VeloxScreen to allow another attempt. No automatic retry or direct printer.cfg editing is performed.

References: [Klipper G-Codes](https://www.klipper3d.org/G-Codes.html), [Cartographer Z offset](https://docs.cartographer3d.com/cartographer-probe/installation-and-setup/software-configuration/z-offset/), [Moonraker printer administration](https://moonraker.readthedocs.io/en/latest/external_api/printer/).

## Test status

Dependency-free tests simulate method selection, blocked states, changed offsets, reset, save requests, reminder transitions and network timeouts. PyQt5 and a physical printer are unavailable in the build environment. Test on a disposable first-layer print before production use.
