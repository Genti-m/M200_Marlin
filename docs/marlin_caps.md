# Marlin capabilities (M115)

Captured 2026-11-18 through the ESP32-S3 bridge (milestone 2), DEBUG header, 250000 8N1.

```
FIRMWARE_NAME:Marlin bugfix-2.1.x (Aug 13 2025 11:29:58) SOURCE_CODE_URL:github.com/MarlinFirmware/Marlin PROTOCOL_VERSION:1.0
Cap:SERIAL_XON_XOFF:0
Cap:BINARY_FILE_TRANSFER:0
Cap:EEPROM:0
Cap:VOLUMETRIC:1
Cap:AUTOREPORT_POS:0
Cap:AUTOREPORT_TEMP:1
Cap:PROGRESS:0
Cap:PRINT_JOB:1
Cap:AUTOLEVEL:1
Cap:RUNOUT:0
Cap:Z_PROBE:1
Cap:LEVELING_DATA:1
Cap:BUILD_PERCENT:0
Cap:SOFTWARE_POWER:0
Cap:TOGGLE_LIGHTS:1
Cap:CASE_LIGHT_BRIGHTNESS:0
Cap:EMERGENCY_PARSER:0
Cap:HOST_ACTION_COMMANDS:1
Cap:PROMPT_SUPPORT:1
Cap:SDCARD:1
Cap:MULTI_VOLUME:0
Cap:REPEAT:0
Cap:SD_WRITE:1
Cap:AUTOREPORT_SD_STATUS:0
Cap:LONG_FILENAME:1
Cap:LFN_WRITE:0
Cap:CUSTOM_FIRMWARE_UPLOAD:0
Cap:EXTENDED_M20:1
Cap:THERMAL_PROTECTION:1
Cap:MOTION_MODES:0
Cap:ARCS:1
Cap:BABYSTEPPING:0
Cap:EP_BABYSTEP:0
Cap:CHAMBER_TEMPERATURE:0
Cap:COOLER_TEMPERATURE:0
Cap:MEATPACK:0
Cap:CONFIG_EXPORT:0
ok
```

Boot banner: `Marlin bugfix-2.1.x`, `Last Updated: 2025-07-23 | Author: (m4cias, Zortrax M200 V01/V03/V04)`, `Free Memory: 38751  PlannerBufferBytes: 1472`.

## What this means for the bridge

| Cap | Value | Consequence |
|-----|-------|-------------|
| `EMERGENCY_PARSER` | 0 | `M112` / `M108` / `M410` are **not** read early. They wait in Marlin's command buffer behind the running command (a long `G28` or `M109` finishes first). The bridge sends them ahead of its own queue and clears that queue, but cannot make Marlin react faster. A real hard stop would need the printer's power or reset, not G-code. |
| `EEPROM` | 0 | Settings can't be saved: `M500` does nothing, and every boot prints "Hardcoded Default Settings Loaded". If a setting must change, the bridge has to send it after every printer boot (`start` line). Resolves the `M500` open point. |
| `AUTOREPORT_TEMP` | 1 | `M155 S2` works: Marlin reports temperatures every 2 s by itself, no polling. |
| `AUTOREPORT_POS` | 0 | Position needs `M114` polling. |
| `AUTOREPORT_SD_STATUS` | 0 | SD progress needs `M27` polling (no `M27 S2`). |
| `PROGRESS`, `BUILD_PERCENT` | 0 | No `M73` progress: compute it from `M27` byte counts. |
| `SDCARD`, `SD_WRITE`, `LONG_FILENAME`, `EXTENDED_M20` | 1 | SD printing (`M20` / `M23` / `M24`) and upload over `M28` / `M29` are possible (milestone 6). `M20 L` gives long names. Boot showed "No SD card" at the time of capture. |
| `HOST_ACTION_COMMANDS`, `PROMPT_SUPPORT` | 1 | Marlin sends `//action:...` lines (`notification`, `prompt_*`, `pause`/`resume`). The web UI can show them. |
| `AUTOLEVEL`, `Z_PROBE`, `LEVELING_DATA` | 1 | Bed probing and `G29` / `M420 V` data are there: useful for the measurement flow. |
| `TOGGLE_LIGHTS` | 1 | A light toggle button is possible. |
| `BINARY_FILE_TRANSFER`, `MEATPACK` | 0 | Plain ASCII G-code only. |

Not in the list: `HOST_KEEPALIVE` (whether Marlin sends `busy: processing`). Check during a `G28`: if no `busy:` lines appear, the `ok` timeout in `printer_link` must be longer than the slowest command.
