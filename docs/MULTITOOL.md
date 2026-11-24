# Multi-tool & filament management (v0.4.8 alpha)

## Tools page

VeloxScreen discovers Klipper objects named `extruder`, `extruder1`, `extruder2`, etc. The default label is T0 for `extruder` and Tn for `extruderN`. This is a naming convention, not proof of a physical tool's identity. Extruder steppers without independent heaters are not counted as separate tools. Shared-nozzle and custom toolchanger systems may require a separate integration.

Select a tile to choose a target. The blue tile is the active extruder from `toolhead.extruder`; a dot marks the selection. Tap **Activate selected tool** to run an existing matching tool-change macro. It is never inferred from a tap alone. Verify the actual mapping on unusual toolchangers. Klipper status is logical state, not a dock/physical presence sensor.

Each tile displays actual / target temperature. The selected heater status is red while heating, green at target, amber while cooling, neutral when off. **Set tool temperature** edits the selected heater, even if it is not active, subject to the configured maximum. Manual extrusion requires a hot active extruder. Load/Unload are enabled only for the selected, active extruder; heating, movement and park details belong to the printer macro.

## Optional configuration

Existing `~/printer_data/config/veloxscreen.conf` is retained. Add or edit these sections only if needed (do not duplicate sections):

```ini
[tools]
T0 = extruder
T1 = extruder1
T2 = extruder2
T3 = extruder3

[tool_commands]
# Default: detected T0/T1/T2/T3 macros. Uncomment only verified alternatives.
# T0 = PICK_TOOL TOOL=0

[filament_macros]
# Defaults are detected if the keys are omitted.
# load = LOAD_FILAMENT
# unload = UNLOAD_FILAMENT
# load_all = LOAD_ALL
# unload_all = UNLOAD_ALL
```

A key with an empty value disables the corresponding button. An override may use `{tool}` (e.g. T2) and `{extruder}` (e.g. extruder2) in filament commands, for example `load = MY_LOAD EXTRUDER={extruder}`. Verify macro name and arguments on the printer first. Commands must be single-line calls to existing `gcode_macro` objects. Tool-change overrides use `[tool_commands]`; no raw ACTIVATE_EXTRUDER fallback is issued. Built-in tool commands not exposed as macros require a wrapper macro.

Default macro aliases: `LOAD_FILAMENT` / `FILAMENT_LOAD`, `UNLOAD_FILAMENT` / `FILAMENT_UNLOAD`, `LOAD_ALL` / `LOAD_ALL_FILAMENT` / `FILAMENT_LOAD_ALL`, `UNLOAD_ALL` / `UNLOAD_ALL_FILAMENT` / `FILAMENT_UNLOAD_ALL`. Matching names do not prove that a macro's semantics or parameters are compatible. Use explicit overrides or disable unverified buttons. All-tool macros own their complete sequence, including heating, tool pickup and shutdown.

## Operation and limits

Local manual filament operations require confirmed idle/ready status. The worker checks macro availability and active heater immediately before dispatch. The UI waits for the macro response, M400 and a fresh status poll. A timeout is not retried; verify the printer state. The 900-second macro timeout is not a motion timer or a guarantee of completion. Delayed/asynchronous macros must provide their own operation interlock. The UI cannot guarantee exclusive access against Mainsail, other clients or printer-side automations. Do not start another job externally while a filament operation is running. The emergency stop remains available.

## Snapmaker U1 / N1

No Snapmaker-specific motion is hard-coded. A four-extruder U1 can use the same mapping shown above when it matches the actual printer. A matching `UNLOAD_ALL` macro will automatically enable Unload All. The U1-specific macro must be validated against the installed `UNLOAD_FILAMENT`, tool-change, homing and park macros; this release does not include a fabricated sequence or alter printer.cfg. Verify each tool independently before an all-tool run.

## Validation

The release has dependency-free logic tests (including sparse tool IDs, macro overrides and fresh dispatch guards). Native Qt rendering, touchscreen interaction, actual tool changes and filament operations require hardware testing. The new image is a reconstructed UI preview, not a connected-printer capture.
