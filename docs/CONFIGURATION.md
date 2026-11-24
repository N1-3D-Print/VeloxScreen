# Configuration reference

VeloxScreen reads the following **printer-local** file:

```
~/printer_data/config/veloxscreen.conf
```

The app also supports a legacy `veloxscreen.conf` next to `main.py` as a fallback, but the preferred location is beside `printer.cfg`. The example file in this repository is [`../veloxscreen.conf.example`](../veloxscreen.conf.example).

**Default language:** `language = en` (English). `de` enables the optional German translation. Some translations may still be incomplete in the alpha.

## Supported groups

| Section | What it controls |
| --- | --- |
| `[connection]` | Moonraker URL and update interval |
| `[appearance]` | Name, font size, language and theme colors (`#RRGGBB`) |
| `[extrusion]` | Extrusion lengths, speeds and axis jog steps |
| `[tools]` | Optional manual T-to-extruder mapping |
| `[tool_commands]` | Optional exact tool-change macro commands |
| `[filament_macros]` | Optional exact filament macro commands / disable keys |
| `[temperature_presets]` | Any number of hotend/bed/chamber presets |
| `[macros]` | Optional quick macro commands |
| `[layout]` | Custom block, navigation and item ordering; optional custom chamber-heater mapping |
| `[behavior]` | Confirmation prompts before G-Code commands |
| `[macro_visibility]` | Show all/selected macros; allowlist/denylist |
| `[led_effects]` | Optional LED effect fade duration |
| `[startup]` | Splash image, animation and duration |
| `[notifications]` | Klipper RESPOND popup visibility/duration |
| `[overrides]` | M220/M221 allowed percent bounds |

## Temperature presets

Use `nozzle:`, `bed:` and `chamber:` only for the heaters you want changed. Presets do **not** create heaters. A temperature-only sensor is displayed without a target control.

```ini
[temperature_presets]
PLA = nozzle:210, bed:60
Hotend only 240 = nozzle:240
Bed only 60 = bed:60
Chamber only 45 = chamber:45
```

Values in this example may not be appropriate for your printer. Check the configured maximum temperature and hardware capabilities before applying them.

## Macros

Macros are read from Klipper and can also be configured as named shortcuts:

```ini
[macros]
Brush nozzle = BRUSH_NOZZLE
```

To hide all except selected macros:

```ini
[macro_visibility]
mode = selected
visible = BRUSH_NOZZLE, PARK_NOZZLE
hidden =
```

This only controls the VeloxScreen UI. No Klipper macro is deleted or disabled.

## Confirmation dialogs

```ini
[behavior]
confirm_commands = true
confirm_shutdown = true
```

Set `confirm_commands` to `false` to remove the normal G-Code prompt. `confirm_tool_change`, `confirm_extrusion` and `confirm_filament` independently override it for their respective operations (including Load/Unload All); omitted keys inherit the general setting. `direct_tool_change = true` enables tile-tap activation; set false to restore a separate Activate button. All options are available in Settings → Confirmation dialogs. **This does not bypass Klipper safety checks.**

`confirm_shutdown` independently controls the Settings → Service Control **Shutdown Host** confirmation (default: `true`), also accessible under Settings → Confirmation dialogs. The button only becomes available when a live idle state is confirmed; the state is checked again immediately before the request. Active/paused prints and unknown state remain blocked even with confirmation disabled. Let the hotends cool before shutting down. Moonraker must have permission to shut down its host OS. This does not switch off a separate printer power supply or change M112/recovery confirmations.

## Speed and flow overrides

```ini
[overrides]
speed_min = 10
speed_max = 300
flow_min = 50
flow_max = 150
```

M220 modifies the G-Code speed factor. M221 modifies the G-Code extrusion factor. These are distinct from motion `max_velocity` and `max_accel`. Custom macro overrides may change the behavior.

## LED effects

```ini
[led_effects]
fade_seconds = 0.5
```

LED effect controls appear when Klipper exposes `led_effect` objects from the optional upstream plugin. VeloxScreen does not package that plugin.

## Wi-Fi network control (experimental)

VeloxScreen uses `nmcli` from NetworkManager to list and connect to Wi-Fi networks. The app user must have NetworkManager permissions (typically via a local PolicyKit session). The interface does not add passwordless sudo rules or save Wi-Fi passwords to `veloxscreen.conf`. Connecting to another network can interrupt remote access; use the display physically and keep a backup connection. WPA-Personal / open networks are supported by the initial implementation.

## Manual jog direction and speed

The Settings page controls manual movement only. Settings are stored in `[jog]`:

```ini
[jog]
invert_x = false
invert_y = false
invert_z = false
speed_x = 100
speed_y = 100
speed_z = 10
```

Speeds are mm/s; X/Y accept 1–500 and Z 0.1–50. Jog commands convert to mm/min for G-code. These settings do **not** change homing or motor directions in Klipper.


## Custom layout

Use **Settings → Customize layout → Edit layout**. Select the category and change its order with Up/Down or optional drag mode. Save applies the order immediately (live object lists refresh on the next Moonraker poll); Cancel discards changes. Reset order resets only the selected category after saving.

```ini
[layout]
fan_order = fan_generic part_t1, fan, fan_generic aux
print_order = ["temperatures", "fans", "motion", "overrides"]
temperature_order = ["heater_bed", "extruder", "extruder1"]
```

Additional keys: `nav_order`, `dashboard_order`, `control_order`, `tools_order`, `temperatures_order`, `macros_order`, `settings_order`, `macro_order`, `preset_order`. The editor writes JSON arrays except `fan_order`, which remains comma-separated for compatibility. Empty values use default order. Unknown IDs are ignored for display; new objects are appended. Fan and temperature order is shared by the relevant pages; the Dashboard includes every detected hotend, the bed and a prioritized chamber reading, with other generic heaters filling spare slots, and Print excludes automatic fans. Safety and fault controls cannot be moved. No Klipper configuration or hardware settings are changed.

## Live heating indicators

The UI compares each reported heater temperature with its target: red below target minus 2 °C, green within ±2 °C, amber above target plus 2 °C. A zero target is off; missing data is unknown. These colors describe temperature relative to the target, not instantaneous heater power. A preset is highlighted when all applicable targets match live Klipper data. Only real heaters participate. No configuration changes are needed.

## Quality of life settings (v0.4.0)

See [QUALITY_OF_LIFE.md](QUALITY_OF_LIFE.md) for the new `[display]`, `[quick_actions]`, `[notifications]` and `[print_view]` options. The new settings are optional; old configurations are preserved. Startup page uses stable internal page IDs (choose it from Settings).

## Live Z calibration (v0.4.1)

No change required for supported standard probes or mechanical endstops. For a single standard `[probe_eddy_current ...]` setup, set `[babystep] eddy_method = scan` or `tap` only after verifying the correct method and command support for your firmware. The default is empty, which blocks permanent Eddy saves. See [Z offset saving](Z_OFFSET.md).

## Pointer style (v0.4.2)

`[display] cursor_style = dot` (default) uses a small white dot with a dark outline inside VeloxScreen, including Qt dialogs. `hidden` hides the cursor; `system` restores the normal OS pointer. Select the style in Settings → Display comfort, save, then restart VeloxScreen. The operating-system cursor theme is not changed.

Dashboard and Print share a compact emergency stop fixed at the top right. Host shutdown moved to Settings → Service Control and retains its separate confirmation and idle checks.

See [Multi-tool & filament management](MULTITOOL.md) for tool mapping, macro overrides and limitations.

## Per-tool filament sensors

Optional `[filament_sensors]` maps T labels to exact Klipper sensor object names, e.g. `T0 = filament_switch_sensor e0_filament`. Unspecified tools use unambiguous name detection. Empty values opt out. See [multi-tool setup](MULTITOOL.md).
