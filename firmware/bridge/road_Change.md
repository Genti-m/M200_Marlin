# Road change: M200 to laser engraver

The M200 becomes a laser engraver. Heating is removed completely. For now only the axes are
controlled. This file grows one chapter at a time, as each step is done.

## 0. Starting point

- The printer still runs the stock Zortrax Marlin (encrypted `Update.bin`, so no self-built
  firmware from SD). Every change is made in the bridge, or in hardware.
- Marlin `kill()`s (needs a power cycle) on:
  - **MINTEMP**: the sensor reads below 5 °C **and** the heater target is above 0.
  - **Heating failed / thermal runaway**: the target is above 0 but the temperature doesn't rise.
  - **MAXTEMP**: the sensor reads too hot. This is checked **always**, even with target 0.
  - **Z homing / probing** (`G28`, `G29`, `G30`, `M48`): `PREHEAT_BEFORE_PROBING` sets
    nozzle 180 / bed 30 first, and Z homes with the nozzle as the probe.
- The rule: **no heater target ever goes above 0, and a sensor input is never shorted.**
  With both, Marlin raises no heating error, even with the heaters gone.

### What's next

- [x] 1. Hardware: heaters and sensors out
- [x] 2. Bridge: refuse every heat command
- [x] 3. Homing without G28 (switch = 0, park at 10) and limits 0..170 mm (defaults, see ch. 6)
- [x] 4. Web page for the laser: no heating, preset frames
- [x] 5. Review fixes: guard parsing, home abort, queue full
- [x] 6. Setting tweaking: limits and homing / move tunables, saved on the bridge
- [x] 7. Foundation: guard host tests, single-write UART lines, API token, OTA rollback, CI
- [ ] 8. Laser safety and laser on/off / power control (kept up to date when the laser goes in)

## 1. Hardware: heaters and sensors out (done)

- The hotend heater, bed heater and both sensors are unplugged at the board, and the loose
  connector ends are insulated.
- The sensor inputs stay **open**. An open sensor reads as cold, which is harmless while the
  target is 0. Never bridge or short them: that reads as very hot and triggers MAXTEMP even
  when idle.

## 2. Bridge: refuse every heat command (done)

`printer_state_allow()` (`components/printer_state/printer_state.c`) refuses these
unconditionally, for both the web and the console. It sends an `Error:bridge: ...` line instead:

| Refused | Why |
|---|---|
| `M104` `M109` `M140` `M190` with S/R above 0 | MINTEMP / heating failed |
| `M303` | PID autotune heats |
| `G29` `G30` `M48` | they preheat nozzle 180 / bed 30 first (`PREHEAT_BEFORE_PROBING`) |
| every `G28` | see chapter 3 |

Target 0 (`M104 S0`, `M140 S0`) still passes. Soft STOP sends it, and it is harmless.
`tools/mock_bridge.py` follows the same rules.

## 3. Homing without G28: X/Y on their endstops, Z stepped by the bridge (done)

- Marlin has no Z endstop. `M119` lists only `x_min`, `y_min` and `z_probe`, so the Z endstop
  header does nothing. The probe is the nozzle ↔ bed pad contact: **a bed calibration pad
  shorted to GND** makes `M119` show `z_probe: TRIGGERED`. (The 3-wire sensor in the head,
  5.5 V / GND / signal, is not the probe.)
- **`G28` can't be used.** It homes Z with the probe even for `G28 R0 X Y`. Without a probe
  contact, Z runs to the top and never stops. With a contact switch, it homes X/Y, goes to
  the bed center, preheats for the probe, and stops Marlin on MINTEMP. The bridge refuses
  every `G28`.
- **The bridge homes**, each home in its own task (`printer_state_home_xy()` / `_z()` in
  `printer_state.c`). Both find the switch, set it as **0** with `G92`, then **park at 10 mm**
  so the switch isn't left pressed:
  - **Home X Y** (web ⌂, console `!homexy`): with endstop checking on (`M120`), X goes fast
    toward min (250 mm, the endstop stops it), 1 mm back, slowly in again. `M119` must show
    `x_min` pressed after each pass. Then the same for Y, then `G92 X0 Y0` and `G1 X10 Y10`.
  - **Home Z** (web ⌂Z, console `!homez`): Marlin reads the Z switch (bed pad wire ↔ GND)
    only while probing, and probing preheats. `G38.2` isn't built in (`Unknown command`). So
    the bridge steps Z: 0.5 mm steps toward −Z with `M400` + `M119` after each, until
    `z_probe` is pressed, then 1 mm back, then 0.1 mm steps until pressed, `G92 Z0`, `G1 Z10`.
    Only absolute moves after a `G92 Z0`, so Marlin is never left in relative mode.
  - While a home runs, user G-code is refused. STOP aborts it, and also sends `G90` + `M121`.
    The result comes as an `echo:bridge:` / `Error:bridge:` line.
- **Limits: 0..170 mm from the switch** (`PRINTER_AXIS_MAX_MM`). Marlin is never homed, so
  it has no soft limits. The bridge tracks each homed axis from the lines it lets through
  (`G90`/`G91`, `G92`, `G0`-`G3`) and refuses any move that would take it outside. A `G92`
  after the home (e.g. a work origin) shifts Marlin's numbers, not the limits. Arcs: only the
  end point is checked. Axes not homed have no limits ("move without homing").
- Every web jog and preset move still runs with endstop checking on (`M120`).
- No emergency parser: STOP waits behind a running command, so a runaway needs the power switch.

## 4. Web page for the laser (done)

- The heating parts are gone: the hotend and bed cards, the temperature chart, and `M105`.
- **Position**: an X / Y / Z badge per axis, green when homed by the bridge.
  Below the numbers, a live frame like the presets: a green dot at the real X Y position and a
  Z gauge, both from the last `M114` (sent after every jog, preset and home), in mm from the
  switches. Refresh is a small icon at the top right of the card.
- Quick jog clicks used to lose the homes: a jog is 7 lines (`M120 G91 G1 G90 M400 M121 M114`),
  the queue holds 16, and a line that didn't fit made the bridge forget the homes (the live
  frame went dim until the next home). Now a click's lines go in whole or not at all: a click
  that doesn't fit is refused (`Error:bridge: busy, ...`) and the homes stay.
- **Move**: ⌂ homes X Y, ⌂Z homes Z. Each jog button works once its axis is homed (or with
  "move without homing"), and jogs stop at the 0..170 limits.
- **Presets**: nine little frames of the XY plane, X and Y at 10 / 50 / 90 % of 0..170 mm.
  The dot shows where the head goes, and the frame the head is on lights up green. They need
  X and Y homed.
- `docs/protocol.md` has the new state fields (`homed` per axis, `mpos`, `off`, `max`,
  `homing`) and the `home_xy` / `home_z` messages. `tools/mock_bridge.py` follows them.

## 5. Review fixes (done, built, not flashed yet)

A review of chapters 2-4 found holes that let a refused or unsafe line reach Marlin.
All are fixed in the bridge.

| Problem | Effect before | Fix |
|---|---|---|
| G-code without spaces (`M104S200`, `G28X`, `N12G28`) wasn't recognized. Marlin needs no spaces | the heat / `G28` refusal was skipped: Marlin heats, MINTEMP kill | `command_code()` reads the code up to the next letter, and `N` numbers without a space |
| Axis words without spaces (`G1X10Y200`): only the first one was read | `Y200` skipped the 0..170 check and the position tracking | `num_param()` starts a word after anything that isn't a letter |
| `G1 Xnan` | NaN passed the limit check and broke the tracking | the check is written so NaN fails it |
| RESTART, or a printer reset, during a home cleared "stopped" / "halted" | a waiting home task could carry on and send its Z steps to a fresh Marlin (wrong position) | `home_abort` flag: set by STOP, kill, reset (`start`) and emergency lines, cleared only by a new home |
| Queue full (16 lines): an allowed line was dropped after the bridge had counted it | the tracked position went wrong. Rapid jog clicks could drop a `G91` and run the `G1` absolute, or drop the `G90` and leave Marlin relative | `printer_state_dropped()`: the homes are lost (home again) and an `Error:bridge: queue full` line is shown. The web batch stops at the first dropped line |

Still open, not changed:

- The web jog clamp uses the last state, so quick clicks can ask for more than the limit.
  The bridge refuses the extra moves, so the web clamp is only cosmetic.
- Z homing logs every 0.5 mm step (`G1`, `M400`, `M119` plus their replies) into the console
  history.
- `tools/mock_bridge.py` still parses only spaced G-code.
- Hardware checks still to do: the X/Y/Z homing directions (`Z_DIR`), and that Marlin carries
  on cleanly after an endstop stops a plain move.

## 6. Setting tweaking (done, built, not flashed yet)

The gear button (top right) opens **Setting tweaking**, an iOS-style sheet with grouped rows.
Each row has a number with +/- buttons, and a dot marks values that differ from the default.
Z direction is a −Z / +Z switch. **Save** checks the values, **Cancel** or a tap outside drops
them, and **Reset to defaults** fills the defaults in (still needs Save).

The settings live **in the bridge** (NVS namespace `laser`, one key each), not in the browser.
So every phone or PC sees the same values, they survive reboots and flashing, and the limits
are still enforced in the firmware for every client (web, console, REST).

| Group | Setting | Default | Range |
|---|---|---|---|
| Limits | Max X / Y / Z from home | 170 / 170 / 170 mm | 10..400 |
| Homing | Park after home | 10 mm | 1..50, below every max |
| | Back off before slow pass | 1 mm | 0.2..5, below park |
| | X Y seek / slow speed | 3000 / 300 mm/min | |
| | Z switch is toward | −Z | −Z / +Z |
| | Z seek / fine step | 0.5 / 0.1 mm | |
| | Z seek / fine speed | 600 / 120 mm/min | |
| Moves | Jog speed X Y / Z | 3000 / 600 mm/min | |
| | Preset speed | 3000 mm/min | |
| | Preset edge | 10 % | 0..40: frames at edge / 50 / 100−edge % |

- A save is refused while a home runs, or when values don't fit together (park ≥ a max,
  slow pass faster than the seek, ...). The sheet shows why, and nothing changes.
- The homing seek now runs up to max + 30 mm (it was a fixed 250).
- **Z direction works both ways**: each axis tracks Marlin's coordinate of its switch and a
  direction, so with +Z the 0..max limits, the park and the web jogs all count away from the
  switch. The state message now has `zero` and `dir` instead of `off`, and `max` is per axis.
- The sheet opens offline too. It shows the last values the bridge sent (kept in the browser),
  or the defaults if it never connected. Save is greyed out until the bridge is online.
- A home uses the settings it started with. A change made later applies to the next home.
- Also: `GET` / `POST /api/settings`, the `settings` / `settings_set` WebSocket messages
  (`docs/protocol.md`), and the mock bridge keeps settings in memory.
- Tested on the mock: settings round trip, a bad value refused, refused while homing, a new
  max X of 100 refusing X120. The sheet was screenshotted at desktop and 390 px phone width.
  Not on the printer yet.

## 7. Foundation fixes (done, built, not flashed yet)

A project review found gaps outside the laser work. All are fixed, the laser is untouched.

| Problem | Fix |
|---|---|
| `wifi_link`, `web/bridge-ui/`, `docs/protocol.md` and `tools/mock_bridge.py` were never committed: a fresh clone didn't build | committed |
| The guard rules (heat, `G28`, limits) had no tests | moved, unchanged, out of `printer_state.c` into the pure-C component `gcode_guard`, with a PC test (`test/test_gcode_guard.c`): unspaced G-code, `N` numbers, NaN, relative moves, `G92` after a home, both Z directions. Run with `sh tools/run_host_tests.sh` (needs gcc / clang) |
| `M104 Snan` passed the heat check (`nan > 0` is false) | the check is written so NaN fails it, like the limits |
| A line and its `
` were two UART writes, so an emergency line from another task could land between them (`G1 X10M112`), and Marlin read one bad line | one write per line (`printer_link.c`) |
| `POST /api/ota` was open to the whole network | **API token** (NVS), set on the USB console: `!token <secret>`, `!token`, `!token clear`. OTA always needs it (`X-Token` header, refused while none is set). With a token set every write needs it too: REST via `X-Token`, the web page asks once and keeps it in the browser. STOP never needs it. Viewing stays open |
| No OTA rollback: an update that breaks WiFi needed USB | `CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE`: a new image is kept only once WiFi is back (120 s), else, or after a crash, the bootloader boots the old one |
| No CI | `.github/workflows/ci.yml`: host tests with ASan/UBSan, and the firmware build with IDF v6.1.1 |
| The mock read only spaced G-code | `parse_gcode()` reads it like `gcode_guard` |

To do once, over USB: the rollback option lives in the **bootloader**, which OTA never
rewrites. Delete `sdkconfig` (it is generated; the new default only applies to a fresh one),
then Build, **Flash** (USB, COM7). Then `!token <secret>` on the console. OTA from now on:

```
curl -H "X-Token: <secret>" --data-binary @build/m200_bridge.bin http://m200.local/api/ota
```

## 8. Laser safety (open: kept up to date when the laser goes in)

Not started on purpose: nothing here is built yet. This chapter collects what has to be in
place **before** the laser module is fitted, and is filled in step by step with that work.

- **Hardware interlock and E-stop** that cut the laser power directly, not through G-code.
  Today STOP waits behind the running Marlin command (no `EMERGENCY_PARSER`), which is not
  enough for a laser.
- **Laser-off watchdog on the bridge**: laser off when the web / WebSocket client that runs
  a job goes away, or the printer link stops answering.
- **Off by default**: laser off at bridge boot, after a printer reset (`start`), on soft STOP
  and on kill.
- How the laser is switched (fan output / `M106`, a spare pin, or a separate driver input),
  and which commands the bridge then lets through or refuses.
- Enclosure, eye protection, fire watch.

