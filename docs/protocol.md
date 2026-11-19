# Bridge protocol: WebSocket + REST

Updated: 2026-11-19 (laser build: no heaters, homing by the bridge, settings; API token). Used by `web/bridge-ui/` and `tools/mock_bridge.py`, implemented by the `web_server` component (milestone 4).
All messages are JSON, one object per WebSocket text frame. Field `t` is the message type.

## WebSocket `/ws`

### Bridge -> browser

| `t` | When | Fields |
|-----|------|--------|
| `hello` | once, right after connect | `name` (hostname), `fw` (bridge version), `auth`: `"required"` (a token is set: send `auth` before any command) or `"open"`, `history`: last lines as `line` objects (max 100), `temps`: chart history, oldest first, one `[seconds_ago, nozzle_act, nozzle_tgt, bed_act, bed_tgt]` per 2 s report, max 300 (10 min; about 3 kB RAM as int16 x10 in the firmware) |
| `settings` | right after `hello`, after every change (from any client), and as the answer to `settings_set` | `v`: `{key: value}`, `d`: `{key: [default, min, max, step]}`. Keys in the table below |
| `state` | on every temperature report (about every 2 s, from `M155 S2`) and on every change | see below |
| `auth` | answer to `auth`, and to a command refused for a missing login | `ok`: bool, `required`: bool (a token is set). `ok:false` means log in again |
| `line` | every line sent to or received from the printer, from any client | `dir`: `"tx"` or `"rx"`, `src`: `web` / `telnet` / `ble` / `console` / `internal` / `job` (tx only), `text` |

`state` fields:

| Field | Type | Meaning |
|-------|------|---------|
| `link` | `"unknown"` / `"up"` / `"no_reply"` | `printer_link_get_state()` |
| `nozzle` | `[actual, target]` or `null` | last `T:` value. The target also changes as soon as an `M104`/`M109` with `S` is sent, and goes to 0 on a kill |
| `bed` | `[actual, target]` or `null` | last `B:` value, target as for `nozzle` (`M140`/`M190`) |
| `busy` | bool | a `busy:` line came within the last 3 s |
| `homing` | bool | a bridge home (`home_xy` / `home_z`) is running: user G-code is refused |
| `homed` | `[x, y, z]` bools | homed by the bridge (no `G28`: it preheats for the Z probe, MINTEMP kill). Cleared by `M84` / `M18` / `start` / `M112` / STOP / kill |
| `mpos` | `[x, y, z]`, `null` per axis not homed | machine position, tracked by the bridge from the lines it lets through: 0 = the switch its home found |
| `zero` | `[x, y, z]` | Marlin's logical position of each switch (a `G92` after the home shifts it) |
| `dir` | `[x, y, z]`, each 1 or -1 | 1: Marlin's numbers grow away from the switch. Z is -1 with setting `z_dir` 1. Logical = `zero + dir * mpos` |
| `max` | `[x, y, z]` mm | settings `max_x/y/z`: a homed axis stays within `0..max` of its switch. The bridge refuses `G0`-`G3` going outside |
| `stopped` | bool | soft STOP pressed. Web G-code is dropped until `restart`. Cleared by `restart` or a printer `start` |
| `halted` | bool | Marlin called `kill()` (`Error:Printer halted. kill() called!`, after `M112`, MINTEMP, MAXTEMP, thermal runaway). Marlin reads nothing more; only a printer reset or power-cycle (`start`) clears it. Temperatures are the last reported values |
| `unplugged` | `{"nozzle": bool, "bed": bool}` | reported temperature below 5 °C (Marlin's MINTEMP): sensor open / heater unplugged. Idle Marlin only reports it, but `kill()`s once that heater's target is above 0, including the preheat a Z home does (`PREHEAT_BEFORE_PROBING`: nozzle 180, bed 30). The bridge refuses heating it and `G28` (with Z or no axis) / `G29` / `G30` / `M48` while set, and answers with an `Error:bridge: ...` line |
| `pos` | `[x, y, z]` or `null` | last `M114` answer (`AUTOREPORT_POS:0`, so the UI asks for it) |
| `rssi` | int (dBm) or `null` | WiFi signal of the bridge |
| `queue` | int | lines waiting in the printer queue |

Example:
```json
{"t":"state","link":"up","nozzle":[182.4,215],"bed":[80.1,80],"busy":false,"homing":false,"stopped":false,"halted":false,"homed":[true,true,true],"mpos":[85,85,10],"zero":[0,0,0],"dir":[1,1,1],"max":[170,170,170],"unplugged":{"nozzle":false,"bed":false},"pos":[100,100,10],"rssi":-52,"queue":0}
```

### Browser -> bridge

| `t` | Fields | Effect |
|-----|--------|--------|
| `gcode` | `lines`: array of strings | each line goes into `printer_send(line, PRINTER_SRC_WEB)`, in order. `M108` / `M410` go to `printer_emergency()` instead, `M112` does the same as `stop`. Other lines pass `printer_state_allow()` first: refused while `stopped` or `homing`, any heat command (target above 0), `M303`, every `G28` / `G29` / `G30` / `M48`, and moves taking a homed axis outside `0..max` (an `Error:bridge: ...` line says why) |
| `auth` | `token` | log this connection in (see Authentication). Answer: an `auth` message |
| `stop` | none | `printer_state_soft_stop()`: queue cleared, `M410` sent at once, then `M104 S0`, `M140 S0`, `M107`, `M84`, `G90`, `M121`. Aborts a running home. Marlin keeps running (no `kill()`, no power-cycle). Without `EMERGENCY_PARSER` Marlin runs `M410` only after the current command. Sets `stopped` |
| `restart` | none | clears `stopped`. Home again before moving |
| `home_xy` | none | `printer_state_home_xy()`: X then Y fast to the min endstop (`M120`), back off, slow in, `M119` must show it pressed; `G92 X0 Y0`, park at X/Y `park` |
| `home_z` | none | `printer_state_home_z()`: steps toward the switch on the probe line (`z_dir`, `z_fast_step`) with `M119` after each, back off, fine steps (`z_slow_step`); `G92 Z0`, park `park` mm away from the switch |
| `settings_set` | any setting keys with numbers, e.g. `{"t":"settings_set","max_x":180}` | keys left out keep their value. Checked, applied and saved in NVS, then a `settings` message to every client. Refused while homing or when out of range / inconsistent (an `Error:bridge: setting ...` line says why, nothing changes) |

Settings (defaults in brackets; firmware: `SETTINGS[]` in `printer_state.c`):

| Key | Range | Meaning |
|-----|-------|---------|
| `max_x`, `max_y`, `max_z` | 10..400 [170] mm | limit from each switch |
| `park` | 1..50 [10] mm | park after a home; below every max |
| `backoff` | 0.2..5 [1] mm | back off before the slow pass; below `park` |
| `xy_fast_f`, `xy_slow_f` | 100..6000 [3000], 30..1500 [300] mm/min | X Y homing seek / slow pass |
| `z_dir` | -1 or 1 [-1] | Marlin Z direction toward the Z switch |
| `z_fast_step`, `z_slow_step` | 0.1..5 [0.5], 0.01..1 [0.1] mm | Z homing step per `M119` check |
| `z_fast_f`, `z_slow_f` | 30..1500 [600], 10..600 [120] mm/min | Z homing feeds |
| `jog_f_xy`, `jog_f_z`, `preset_f` | [3000], [600], [3000] mm/min | web page jogs and preset moves |
| `preset_edge` | 0..40 [10] % | preset frames at edge / 50 / 100-edge % |

The slow pass can't be faster or coarser than the seek. The seek runs up to `max + 30` mm.

Unknown messages are ignored. Lines longer than 96 chars are dropped (`PRINTER_LINE_MAX` = 128 including `N.. *cs`).

## REST

| Method + path | Body | Answer |
|---------------|------|--------|
| `GET /api/state` | | the `state` object (without `t`) |
| `POST /api/gcode` | `text/plain`, one G-code line per text line | `{"queued": n}` (token) |
| `GET /api/settings` | | `{"v": {...}, "d": {...}}` like the `settings` message |
| `POST /api/settings` | JSON object of setting keys | `{"ok": true}` or `{"ok": false, "error": "..."}` (same as `settings_set`; token) |
| `POST /api/stop` | | `{"ok": true}` (same as `stop`; never needs the token) |
| `POST /api/restart` | | `{"ok": true}` (same as `restart`; token) |
| `POST /api/ota` | raw app image (`build/m200_bridge.bin`) | `{"ok":true,"rebooting":true}`, then the bridge boots the new image. **Always** needs the token (403 while none is set). See Authentication |

The UI files themselves are `GET /`, `/style.css`, `/app.js` (gzipped, embedded at build time from `web/bridge-ui/`).

## Authentication

The API token lives in the bridge's NVS and is set on the USB console: `!token <secret>`
(8..64 printable characters, no spaces), `!token` (set or not), `!token clear`.

- **No token set**: everything is open, except `POST /api/ota`, which answers 403.
- **Token set**: every write needs it. REST: header `X-Token: <secret>` (else 401).
  WebSocket: send `{"t":"auth","token":"<secret>"}` first; until then commands get
  `{"t":"auth","ok":false,"required":true}` and do nothing. The web page asks once and keeps
  the token in the browser (`localStorage`). Changing or clearing the token logs every open
  page out.
- **Always open**: `stop` / `POST /api/stop` (anyone may stop the machine), and reading:
  `hello`, `state`, `line`, `settings`, `GET /api/*`, the UI files.

OTA example: `curl -H "X-Token: <secret>" --data-binary @build/m200_bridge.bin http://m200.local/api/ota`.
A new image is kept only once its WiFi is back (within 120 s); otherwise the bootloader boots the previous one.
