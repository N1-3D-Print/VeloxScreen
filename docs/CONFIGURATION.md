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
| `[temperature_presets]` | Any number of hotend/bed/chamber presets |
| `[macros]` | Optional quick macro commands |
| `[layout]` | Fan ordering, optional custom chamber-heater mapping |
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

Set `confirm_commands` to `false` to remove the normal G-Code prompt. **This does not bypass Klipper safety checks.**

`confirm_shutdown` independently controls the Dashboard **Shutdown Host** confirmation (default: `true`), also accessible under Settings → Confirmation dialogs. The button only becomes available when a live idle state is confirmed; the state is checked again immediately before the request. Active/paused prints and unknown state remain blocked even with confirmation disabled. Let the hotends cool before shutting down. Moonraker must have permission to shut down its host OS. This does not switch off a separate printer power supply or change M112/recovery confirmations.

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
