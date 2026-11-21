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
- [x] 8. Laser control in the bridge: arm, keep-alive, GRBL laser mode on a fan output, focus pulse
- [ ] 8b. Laser hardware: module, supply voltage / current, wiring, enable pin, E-stop (open)
- [x] 9. Review fixes 2: guard holes (`M 104`, `M104 I0`), nothing runs after a STOP
- [x] 10. Web page: camera row above the console

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

## 8. Laser control (done, built, not flashed yet; the laser hardware is open, see 8b)

The bridge logic for a laser, built before the laser is chosen. The test module from a Meterk
mini engraver (two wires, battery 3.7 V with USB 5 V through a diode-OR, about 3.8..4.7 V at the
laser) is **not** the one that goes in: a proper module comes later. So nothing here depends
on its voltage or current; those are open in 8b.

**How it is switched.** Stock Marlin has no laser feature and can't be rebuilt, so the laser
hangs on a **Marlin fan output** (`M106 P<laser_fan>`), optionally behind an **enable pin** of
the ESP32 (`CONFIG_LASER_ENABLE_GPIO`, menuconfig "Laser") in series. The enable pin is the
bridge's own cut that doesn't wait for Marlin's queue; the timing comes from Marlin, and it is
coarse (checked in Marlin bugfix-2.1.x source):

- `M106` stores the speed, and every planner block made after it carries it, so the change
  follows the move order. But the fan output is only written in a **10 Hz poll**
  (`check_axes_activity()`, "limit to 10Hz"): a change lands **up to ~100 ms late**. At
  1000 mm/min that is up to 1.7 mm of laser into the next `G0` travel, or missing at the start
  of a `G1`. Segments shorter than the poll may get no power change at all.
- So: outlines and marking at modest speed, yes. Raster / greyscale (power changing every
  fraction of a mm), no. Sharp starts and ends need Marlin's `LASER_FEATURE` (a rebuilt Marlin,
  over SWD) or a driver timed by the ESP itself.

**GRBL laser mode** (what LaserGRBL / LightBurn write), turned into M106 by the bridge
(`gcode_laser_plan()` in `gcode_guard`, host-tested):

| Line | What the bridge sends |
|---|---|
| `M3 S500` / `M4 S500` | nothing, a note: the power for the next moves (`M4` = `M3`, no speed scaling in stock Marlin) |
| `G1` / `G2` / `G3` `[S..]` | `M106 P0 S<pwm>` before the move when the power changes |
| `G0`, `G4` | `M106 P0 S0` before it if the laser was on: travel and dwells don't fire (after the poll lag above) |
| `M5` | `M106 P0 S0` |
| `M106` on the laser's fan with power | refused: use `M3` / `M5` (`M106 S0`, `M107` pass) |

PWM = S / `laser_s_max` x `laser_cap` x 255. Every line still passes the old guard first
(heat, `G28`, the 0..max limits).

**Arming and the keep-alive.** Laser G-code fires nothing until the laser is **armed**: Laser
card on the page, Arm, tap again to confirm. It needs X and Y homed, the printer up, not
stopped or homing. Armed, the page says "alive" every 0.7 s **while it is open and visible**;
the bridge disarms after `laser_lease` (3 s) without one. Hidden tab, closed page, locked phone,
WiFi gone: disarmed.

**Off by default, and every way back to off:**

| Event | Laser |
|---|---|
| bridge boot | enable pin driven off as soon as `printer_state` starts (about the first second after a reset; before that only the external pull-down holds it), `M106 P<fan> S0` to Marlin (it may still run from before) |
| Disarm button, `!laser off`, `POST /api/laser {"arm":false}` | pin off; if it may be firing or lines are queued: soft STOP, else an off line |
| keep-alive ran out | same as Disarm |
| STOP | pin off, `M106 P<fan> S0` right after `M410` |
| homes lost (`M84`, queue full), `M410` / `M108` typed | disarmed (STOP / off line) |
| printer reset (`start`) | disarmed (Marlin boots with the fan off) |
| Marlin kill | pin off. Marlin reads nothing more, and `kill()` / `minkill()` **don't zero the fans** (source); `minkill()` also turns interrupts off, so a software-PWM output freezes as it is, possibly fully on. **Only the pin or the E-stop cuts the laser then** |
| printer stops answering | disarmed, soft STOP |
| laser on, nothing new queued for 1 s | `M400` + off: off once the moves are done (GRBL: off when motion stops) |

Also: settings can't change and homes can't start while armed; the page's jogs and presets are
`G0` now (travel: the laser goes off before them). A laser line and its move go into the queue together, no other
client's line between them.

**Focus pulse**: armed, head standing still, `M5`: `M400`, `laser_test` % (2), `G4 P300`, off.
On and off each land at Marlin's next 10 Hz poll, so the pulse is 300 ms give or take 100.

**Settings** (group Laser in Setting tweaking): `laser_fan` 0, `laser_s_max` 1000,
`laser_cap` 100 %, `laser_test` 2 %, `laser_test_ms` 300, `laser_lease` 3 s.

**API**: WebSocket `laser_arm` / `laser_alive` / `laser_disarm` / `laser_test`,
`POST /api/laser`, state field `laser` (`docs/protocol.md`). USB console: `!laser`, `!laser off`.

Checked: host tests (new: PWM and cap, the GRBL mode line by line, refusals, queue cost), the
firmware builds with no new warnings, and the whole flow against `tools/mock_bridge.py` (same
rules): arm refused before homing, M3 only a note, `G1` gets `M106 P0 S128` before it, `G0`
off first, `M106 S255` refused, settings refused while armed, idle off after 1 s, focus pulse
lines, keep-alive timeout disarms, `M3` refused disarmed, STOP disarms. Page screenshotted
desktop and 390 px. Not on the printer yet.

### Review of ch. 8 (2026-11-21)

Checked against Marlin's own source (bugfix-2.1.x `planner.cpp`, `MarlinCore.cpp`,
`M106_M107.cpp`) and the ESP-IDF headers. Fixed:

| Was | Now |
|---|---|
| The docs said Marlin applies a fan speed "with the planner block after it" | true for the order, but the output is written in a 10 Hz poll: up to ~100 ms late. Written down above, with what that rules out (raster) and how to measure it |
| "Whether kill() stops the fans isn't known" | it doesn't: `kill()` / `minkill()` never zero the fans, and `minkill()` stops interrupts. The note on a kill says the output stays as it was |
| Enable pin set up with `gpio_reset_pin()`, which turns the internal pull-up on: a moment toward "on" for an active-high driver | off level first, then `gpio_config()` with the internal pull toward off |
| Only the UART pins were refused as enable pin | also USB 19/20 (the console), 26-37 (flash, octal PSRAM) and the strapping pins 0, 3, 45, 46 (a pull-down there changes the boot mode). Arming is refused with such a pin |
| "Enable pin off before anything else" at boot | off once `printer_state` starts; before that only the external pull-down |
| Arm right after a disarm: the disarm's STOP could still come and stop the new session | arming waits until the last disarm is finished ("still turning off, try again") |
| Mock: after a fake kill + restart the laser PWM stayed at its old value | reset at `start`, like the firmware |

Confirmed as written: `M106` without `S` is full speed (255), an unknown `P` is ignored, `M106`
never calls `planner.synchronize()`.

### 8b. Laser hardware (open)

To decide when the real module is there:

- **Module and its supply**: voltage and current (a TTL/PWM driver input, or two wires switched
  on the supply). Never the printer's 24 V on a 5 V module. Its own supply (buck) or a big
  capacitor, so the laser's current steps don't reset the ESP32.
- **Which fan output**, and how Marlin drives it, measured with an LED before the laser:
  which `P` index responds, 24 V on + with only − switched (then laser + from its own supply,
  − to the fan −, grounds joined), the PWM frequency, and `FAN_MIN_PWM` / `FAN_KICKSTART` (a
  kick-start would fire full power for a moment). Measure the lag of the 10 Hz poll: an LED on
  the output, `G1 X50 F1000 / M106 S255 / G1 X100 / M106 S0 / G0 X150`, filmed: how far into
  the `G0` it stays lit. Whether the build has `LASER_SYNCHRONOUS_M106_M107` (unlikely without
  `LASER_FEATURE`) can't be read from outside.
- **Enable pin**: a free ESP32-S3 GPIO (the firmware refuses 0, 3, 17-20, 26-37, 45, 46), a
  logic-level MOSFET (AO3400 / IRLZ44N, not IRF520), a pull-down so it is off while the ESP
  boots. Then `CONFIG_LASER_ENABLE_GPIO`.
- **E-stop / key switch** in the laser supply, not through G-code (STOP still waits behind the
  running Marlin command: no `EMERGENCY_PARSER`).
- **Long moves**: the idle-off sends `M400`; if Marlin sends no `busy:` while it waits, a move
  longer than the 15 s `ok` timeout reads as "printer stopped answering" and STOPs.
- Enclosure, goggles for the wavelength, sacrificial board over the bed, smoke extraction,
  never unattended.


## 9. Review fixes 2 (done, built, not flashed yet)

A second project review found two holes in the heat guard and a race around STOP. Checked
against Marlin's own source (bugfix-2.1.x `parser.cpp`, `M104_M109.cpp`, `M140_M190.cpp`).

| Problem | Fix |
|---|---|
| Marlin skips spaces between the letter and the number, the guard didn't: `M 104 S200`, `M 140 S30`, `G 28`, `G 29` went through and Marlin ran them (MINTEMP kill) | `gcode_command_code()` skips them like Marlin. The heater-target display, the axis tracking and the mock (`parse_gcode()`) read them the same way. `M 112` / `M 410` now also count as emergency commands |
| `M104 I<n>` / `M140 I<n>` (heat to material preset n, `HAS_PREHEAT`) passed: the guard only read `S` / `R`, and in Marlin `I` wins over `S` | any heater command with an `I` value is refused, whatever `S` says |
| STOP race: the home task checked "aborted?" and then queued its next move, unlocked. A STOP in between let that move run **after** the STOP's `M84 / G90 / M121`. Same for a web / console line that passed the guard just before the STOP. A typed `M410` / `M108` was worse: the home only saw it later, from the event loop | **epochs** in `printer_link`: every emergency command starts a new one. A sender reads the epoch *before* it decides and queues with `printer_send_at()`; the TX task drops any line from an older epoch. The home keeps the epoch it started with, so any emergency aborts it and drops its queued lines, and a dropped `G92` can no longer mark an axis homed |
| A typed `M410` / `M108` that aborted an X/Y home left Marlin in `G91` with endstops on (only the STOP button sends `G90 / M121`) | after a failed or aborted X/Y home the bridge sends `G90`, `M121` (not after a soft STOP, which sends them, or a kill) |
| `printer_state_allow()` printed a refusal from a shared static buffer after releasing the lock: two refusals at once could swap their messages | copied under the lock |

New host tests: `M 104 S200`, `M  140 S30`, `N7 M 109 R180`, `M104 I0`, `M140 I1`,
`M190 S0 I0`, `M109I0`, `G 28`, `G 29`, `M 48`, plus `gcode_command_code()` with spaces and
`gcode_emergency_code()`.

Cleanups in the same step, no change in behaviour except where noted:

| Before | Now |
|---|---|
| Console and web each did emergency check, epoch, guard, queue, "queue full" on their own: a new client (job, telnet) could forget a step, the epoch above all | one path, `printer_state_submit()`. `printer_state_allow()` / `_dropped()` are private now |
| Two G-code readers: `printer_link` read `M112` / `M410` itself, `gcode_guard` everything else (that's how `M 112` slipped past one of them) | `gcode_emergency_code()` in `gcode_guard`, host-tested; `printer_link` only writes. It now also reads `N5 M112` as an emergency, like Marlin |
| `link_name()` in the web server copied `printer_link_state_name()` | one function, lowercase (`up`, `no_reply`, `unknown`): the console's `!net` and the log now print it lowercase too |
| `printer_state_set_settings()` / `refusal()` returned a shared static buffer | the caller passes the buffer (`PRINTER_STATE_WHY_MAX`) |
| Saved settings that don't fit together fell back to defaults silently | same, with a warning in the log |
| Docs: `newStatus.md` pointed to files not in the repo, listed Preheat / hotend buttons, said "confirm during a real `G28`"; `protocol.md` still said the heat refusal depends on `unplugged`; the README had no word on a Windows compiler | updated; `esps32Plan.md` got a note that it is the original plan |

Kept on purpose: `PRINTER_SRC_TELNET` / `_BLE` (milestone 5), `PRINTER_SRC_JOB` and
`marlin_format_numbered()` (job streaming).

Status: the firmware builds (IDF v6.1.1), no new warnings. The host tests pass on this PC
(MSYS2 UCRT64 gcc 16.2, which `tools/run_host_tests.sh` now finds by itself), and 12 of the
new cases fail against the old guard, so they really cover the holes. To check after flashing: `M 104 S200` and `M104 I0` on the console give
`Error:bridge: ... heating refused`; STOP during an X/Y home leaves no move afterwards.

## 10. Web page: camera row (done, built, not flashed yet)

A **Camera** row sits above the console, full width (under the Presets on a phone). The source
is chosen in Setting tweaking, group **Camera**:

| Setting | Values |
|---|---|
| Source | Off / Stream (MJPEG, shown live) / Snapshot (a JPEG, loaded again every N s) |
| URL | any `http://` / `https://` picture URL. ESP32-CAM: `http://<camera>:81/stream` or `http://<camera>/capture`; mjpg-streamer: `?action=stream` |
| Snapshot every | 1..60 s |
| Rotate / Mirror | 0 / 90 / 180 / 270°, mirror on / off |

- **Kept in the browser only** (`localStorage`), on purpose: the bridge never sees the camera
  data, and each phone / PC picks its own. No firmware or protocol change; the sheet's Save
  stores it even while the bridge is offline.
- Pause (frees a camera that allows one viewer), reconnect, full screen. A hidden tab stops
  the stream; an unreachable camera shows a message and retries every 5 s.
- The viewing device's own webcam can't be used: browsers allow that only on https, the
  bridge serves plain http. A camera has to serve its picture over the network itself.

Checked in headless Chrome against `tools/mock_bridge.py` with a test picture: placement
above the console, 0 / 90 / 180° + mirror, phone width without side scroll, unreachable
camera, bad URL refused on Save, Off hides the row's buttons. No console errors.

A USB camera on the PC is served by `tools/usb_cam.py` (OpenCV): `--list` shows the cameras
that give a picture, `--cam N` serves one at `http://<pc>:8081/stream` and `/snapshot`, one
capture shared by every viewer.

**The USB camera works** ("USB Live camera", Sonix 0C45:6537, `--cam 1`): 640x480 at 15 fps
over the LAN, shown live in the page. What stood in the way:

- The maker's app (**Angetube Camera**) held the camera. Close it before starting usb_cam.py.
- Asking the camera for a frame rate (`CAP_PROP_FPS`) stalls it: every read timed out after
  10 s (MSMF `E_PENDING`), which looked like a hang. The script never sets it now; `--fps`
  only paces the server.
- The script uses **DirectShow** by default (was Media Foundation): only DirectShow can
  change the camera's settings, and it gives HD as MJPEG (`--size 1280x720` about 9 fps,
  1920x1080 about 3 fps, still on the hub). Same numbers here: cam 0 built-in, cam 1 USB.

### Camera settings in the page (the Angetube app's controls)

A **sliders button** in the Camera row opens the camera's own settings: brightness, contrast,
saturation, hue, sharpness, gamma, gain, backlight compensation, exposure (Auto), white
balance (Auto), focus (Auto), zoom. Moving a slider switches its Auto off, like the driver
does; Reset puts back the camera's values from the first start.

- `tools/usb_cam.py` serves them at `GET /controls` and changes them with
  `POST /controls` (`{"brightness": {"value": 20}, "wb": {"auto": true}}` or
  `{"reset": true}`), with CORS so the bridge's page can call the PC directly. The bridge
  still never sees the camera.
- The driver gives no ranges through OpenCV, so each one is found at start by trying values
  (DirectShow refuses out-of-range ones, about 1 s for all). Exposure accepts any number:
  its range is fixed at -13..-1 (log2 s). Exposure doesn't report Auto: the script keeps it,
  on by default (the room is dark: a fixed -6 gives a black picture).
- Values are saved in `~/.m200_usb_cam.json` and applied again at every start.
- The button shows only for a camera that answers `/controls`, asked once per camera (and
  again after it was offline); an ESP32-CAM gets no button. Slider moves are sent at most
  one request at a time, merged meanwhile. Every viewer changes the same camera.

Checked in headless Chrome against the real camera: all 12 settings listed with their
ranges, a quick drag ends on the last value, Auto on then slider = manual, phone width
without side scroll, Reset, no button for a camera without `/controls`. No console errors.

## 11. Bed scan: tiles and collage (PC tool, no firmware change)

`tools/scan_bed.py` photographs the bed in a grid of overlapping tiles and puts them together
into one picture. It drives the head through the bridge's REST API and takes the pictures from
`tools/usb_cam.py`. Firmware and page unchanged.

- **Setup**: bridge reachable, X/Y homed (Home XY in the page: G28 is refused), not stopped or
  halted. Z is never moved. The limits (`max_x/y`) and the feed (`jog_f_xy`) come from the
  bridge's settings. Coordinates are mm from the switches, the frame the guard checks.
- **Camera locked** for the scan through `/controls`: exposure matched to what auto gave (same
  brightness), focus set by sharpness, white balance on its slider value (or `--exposure`,
  `--focus`, `--wb`). Put back at the end.
- **Calibration**: `--check` moves the head 2 mm in X and in Y and measures the picture shift
  (phase correlation). That tells whether the camera rides on the head, the mm/px, and the
  rotation/mirror (same order as the page: mirror, then rotate clockwise). `--calibrate`:
  click two marks on a ruler and type the mm. Both save `scans/calibration.json`.
  `--lens-calibrate` + `--undistort`: optional checkerboard lens correction.
- **Grid**: tile centres over the bed (or `--area`), `--overlap 0.25`, snake order.
- **Per tile**: one batch `G90 / G0 X Y F / M400 / M114`. The `queue` count leaves out the line
  in flight, so the move counts as done when `/api/state` `pos` (the M114 answer, which M400
  holds back until the move has ended) equals the target. Then settle 0.5 s, throw away one
  snapshot, keep the next new one: `scans/<date_time>/tile_rRR_cCC.jpg` + `tiles.json`.
- **Collage**: each tile placed by its X/Y x px/mm (feathered overlaps, or `--blend crop`).
  `--stitch` also tries `cv2.Stitcher` (SCANS mode). `--collage-only`, `--resume`, `--dry-run`;
  Ctrl+C leaves the head where it is.

Checked: a synthetic bed cut into rotated + mirrored tiles comes back matching the original
(mean error 2.4/255, JPEG noise), and `--check` finds that rotation/mirror and the scale
(0.252 for 0.25 mm/px). Against `tools/mock_bridge.py` with the real camera: 6 tiles in 19 s,
`--resume` re-shoots only the missing ones, settings put back, `--check` reports a fixed camera
(the mock's head doesn't carry it). Found on the way: on Windows `localhost` tries IPv6 first
and costs 2 s per request to usb_cam.py; the script looks each host up once, IPv4.
Guards: a black or white picture (light off, lens covered) stops the scan before any move;
a camera that refuses manual exposure/focus (seen once at night: every `/controls` change
ignored) gives a warning; bridge or camera gone mid-scan stops cleanly, settings put back.
### First real scan: the collage was wrong, fixed (2026-11-20)

The first 4 x 3 scan on the printer (01:50) gave a broken collage: ruler pieces didn't line up,
features doubled, diamond moire in every overlap. Measured from the tiles themselves:

- The camera is **turned about 3 degrees** against the axes (not a whole 90). Only 0/90/180/270
  were possible, so each tile edge was about 30 px out.
- The ruler scale was right (0.1199 vs 0.1195 mm/px measured).
- Feathering two hole patterns a few px apart makes moire.
- The first scan (01:48) was blurry and changed brightness halfway: the camera wasn't locked
  (two usb_cam.py were running and fought over the camera). Not a collage problem.

Now the collage measures a 2x2 mapping (mm per raw pixel: scale, any angle, mirror) from the
neighbour overlaps: ORB matches, only near the expected shift, and the best cluster must beat
the second by 2x (the holes and the edge screws repeat). Least squares over the pairs, twice
(wide gate around the calibration, then tight). Each tile is warped onto the canvas with it.
Default blend is now crop (each pixel from the tile whose centre is nearest), and a flat field
(median of the blurred tiles) removes the bright spot the light makes in every tile (+-13 %).
The mapping is saved in `scans/calibration.json` for scans too plain to measure.

Checked: a synthetic bed with a 2.5 mm hole grid, camera at 0.128 mm/px turned 3 deg, told
0.12 and 0 deg: measured 0.12806 mm/px and 3.00 deg from 29 pairs, error vs truth 43 -> 7.7.
The real 01:50 scan: 6 pairs, 0.1195 mm/px, -2.8 deg, ruler continuous 8..24 cm, metal square
whole. Left: about 6 px residual at the seams (lens distortion: `--lens-calibrate` +
`--undistort` should take it), and the metal square looks different from each angle.
`scans/` is in `.gitignore`.

### Live view and a better overlap measurement (2026-11-20)

- `--show`: a window with one cell per tile, laid out like the bed (+Y up), filled in as the
  photos are taken (yellow frame: moving there, green: just taken), then the collage in a
  second window. Saved as `contact.jpg` next to the tiles.
- Second real scan (02:02, 4 x 3, max 180 mm now): the ORB matching found only 1..21 good
  matches per pair (the plate is mostly identical holes) and measured nothing. Replaced by
  phase correlation of the strip both tiles show, cut where the current mapping predicts it,
  strength >= 0.06, three passes, pairs the fit can't explain dropped. Result: 12 of 17 pairs,
  rms 2.6 px (was 6 px with ORB on the 01:50 scan); 01:50 now 15 pairs, rms 3.9 px. Both
  scans agree: 0.1188 / 0.1187 mm/px, camera turned -2.7 / -2.6 deg. Synthetic test still
  exact (0.12799 for 0.128 mm/px, 93.00 for 93 deg).

Not done yet: a scan with the lens correction.

## 12. Web page: Scan and Collages buttons (done, built, not flashed yet)

Two buttons in the Camera row start a bed scan and show the last ones. The scan still runs on
the PC: the camera is there, the bridge never sees a picture.

- `tools/usb_cam.py` runs `scan_bed.py`'s scan in a thread (one program holds the camera):
  `POST /scan/start` with `{"bridge": <the page's own address>, "token": <the page's token>}`,
  `GET /scan/status` (state, plan, tiles done, the one being moved to, last log lines),
  `POST /scan/stop` (before the next move; the head stays, camera settings put back),
  `GET /scans` (last 12), `GET /scans/<name>/<tile|collage|contact>.jpg[?w=N]` (tiles turned
  like the collage; only those names, no paths). The head moves with the page's token, so the
  bridge's login decides as for any other command.
- Page: the buttons show only for a camera server that answers `/scan/status` (like the sliders
  button and `/controls`). **Scan** opens a sheet: Start (enabled once X/Y are homed and the
  printer is ready), then the grid laid out like the bed (+Y up) fills in tile by tile (orange:
  moving there, green: just taken), sized to fit the screen; then the collage (tap: full size).
  Start turns into Stop while it runs. **Collages** lists the last scans with a thumbnail,
  date, tile count, and links to the collage and the tile grid.
- `scans/` is now always the repo root's, from any working directory.
- A fit the tiles can't support is no longer used: rms over 8 px or the scale 25 % off the
  calibration -> placed by the saved mapping. Found by a test with a fixed webcam (rms 131 px,
  "0.145 mm/px, +12.5 deg" had been used for the collage).

Checked in headless Chrome against `tools/mock_bridge.py` with a camera server on the PC's
built-in webcam (port 8082; the USB camera's server was left running): buttons appear,
Start -> 12 cells, "tile 6/12: moving to row 1 col 2" with r1c2 orange and r1c3 green (snake
order), row 0 at the bottom, all 12 tiles and the collage load, Collages lists the scans with
thumbnails, phone width without side scroll, no console errors. Path tricks
(`..%2F..`, `tiles.json`) answer 404, a bad bridge address 409. Firmware built with the new
page (app.js.gz contains it).

### Cleaner tiles, focus held (2026-11-20)

- **Focus held where autofocus had it** when the scan starts (it was searched, or jumped to the
  old slider value 520). Cause: switching Auto off made `usb_cam.py` write the stored slider
  value back. Now Auto off writes no value, so the camera stays where automatic left it
  (focus, white balance). Reading the live value doesn't work: DirectShow reports the last
  value set by hand while Auto is on (built-in webcam: set 6500, Auto on, reads 6500 for 4 s).
  Focus is locked first, before exposure changes can make autofocus hunt. A sharpness check
  before/after the lock falls back to the search only if the lens moved anyway (< 70 %).
- **Cleaner tiles**: `usb_cam.py` `GET /frame[?after=seq]` gives the next frame uncompressed
  (PNG, `X-Frame-Seq`). Each tile is the average of `--frames 4` of them taken after the
  settle, frames below 80 % of the sharpest left out (head still shaking), saved as JPEG 95.
  Noise drops by about 2x (sqrt 4) and there are no stream-JPEG blocks.
- `--calibrate` drops the saved mapping (the camera may have been moved).

Checked on the built-in webcam (the USB camera was unplugged while being remounted): `/frame`
gives newer frames, Auto WB off without a write kept auto's colour, a 2-tile scan with the
averaged capture against the mock. Still to check on the USB camera: the focus stays sharp
after the lock, and the noise gain on a lit bed.

### After the camera was remounted: collage wrong, tiles shown upright (2026-11-20)

Scans 03:25 and 03:29 (camera remounted, then moved again) gave a messy collage and tiles shown
standing up in the page. The camera is now turned 180 deg (was 270) and higher (0.149 and
0.193 mm/px, was 0.119). The overlap measurement looks for the shift near the calibration's
guess, so it found nothing, and the tiles were placed and shown with the old mounting.
Comparing whole pictures without a guess doesn't work on this plate: the camera-fixed light
spot and the moire win, or a neighbouring hole of the grid.

The hole grid is the fix. Its spectrum (2D FFT, light and pixel noise filtered out) gives the
hole spacing in px and the grid angle in every tile. The spacing is a fixed size on the plate
(1.814 mm, from the 02:02 scan), so it gives the scale of any picture; the angle gives the
camera's turn up to 90 deg.

- The collage measurement starts from the calibration; if the tiles don't confirm it, from
  the hole grid's scale and turn with each of the four 90 deg choices. A result counts only
  if the tiles agree (rms <= 8 px) and the scale matches the hole grid (10 %).
- A trusted result is saved in `scans/calibration.json` (scale, rot / flip to the nearest
  90 deg, the mapping, the hole-grid reference) only if the scan is newer than the one it
  came from (rebuilding an old scan doesn't undo a remount). The scan's `rot` is updated
  too, so its tiles show upright in the page and in the tile grid.
- At the start of a scan the first picture's hole grid is compared with the calibration: a
  moved camera (scale > 5 % off or turned > 3 deg) is reported and the grid is planned with
  the new scale and a square field (the turn may be 90 deg), before any move.

Result: 03:29 measured from all 17 pairs, rms 3.3 px, 0.1494 mm/px, rot 180 + 1.7 deg; 03:25
11 pairs, rms 3.0 px, 0.1930 mm/px. Collage: plate whole, ruler straight along the front,
edge screws in line. The next grid: 3 x 4 tiles of 95.6 x 71.7 mm. 02:02 still the same
(12 pairs, 2.6 px). Synthetic test unchanged.

### Left and front strips missing (2026-11-20)

Scan 03:39 measured well (17 pairs, 2.4 px) but the collage had no left and no front edge of
the plate, while it showed the wood beyond the right edge. The grid kept the head half a
picture inside each end of the travel (X 47.8..132.2, Y 35.8..144.2): right only if the
camera looks straight at the nozzle. It sits beside it, so its view is shifted and the left
and front strips were never photographed. Now the head goes over its whole travel by
default, 1 mm off each end (0 is where the home switch triggers): X/Y 1..179 here, 4 x 5 =
20 tiles instead of 12. `--area` still limits it.

### 4 x 4 grid, tiles placed where the photos say (2026-11-20)

- Scan 03:44 still had the old 3 x 4 plan (head from X 47.8): `usb_cam.py` keeps the
  `scan_bed.py` it loaded at start, so it must be restarted after an update. That was the
  "skipped" first column.
- Default overlap 15 % (was 25 %): 4 x 4 = 16 tiles over the whole travel with this camera
  (17 % overlap in Y, 38 % in X), enough to measure. `--grid CxR` fixes the counts.
- Each tile is placed where its overlaps say it was, not only where the head was sent: the
  neighbour pairs that match well give the distances between the pictures, least squares
  finds the positions (commanded positions as a weak pull). 03:39 and 03:29: corrections up
  to 0.4-0.5 mm (backlash). Synthetic: a tile recorded 2 mm off placed 0.25 mm from the truth.
- 03:44 is mechanically wrong: every X move also moved the picture ~1.1 mm in Y, the last row
  5.7 and 6.6 mm (strong matches, so real). 03:39, five minutes earlier, is within 0.5 mm. A
  loose camera mount swinging with the head, or Y skipping steps. The collage now says so
  ("corrected by up to 5.86 mm ... loose camera or skipped steps?").
- Only a clean fit (rms <= 4 px) becomes the calibration: 03:44's 7.3 px fit had replaced the
  good 03:39 one (put back).

### Head range set by hand: X 5..135, Y 2..160, 4 x 4 (2026-11-20)

The camera sits beside the nozzle, so the useful head range is not the whole travel: with
this mount the bed is covered by the head going X 5..135 and Y 2..160 (found by eye from the
collages). `--head-area x0,y0,x1,y1` gives the first and last head position exactly,
`--grid 4x4` the counts; `--save-settings` keeps both in `scans/scan_settings.json`, which
every scan uses by default, including the page's Scan button. Saved now: 5,2,135,160 and 4x4
(X 5 / 48.3 / 91.7 / 135, Y 2 / 54.7 / 107.3 / 160; overlap 55 % in X, 26 % in Y).
The last scans (03:44, 03:49) still had the old 3 x 4 plan: the camera server had been
started at 03:20 and runs the `scan_bed.py` it loaded then. Restart it after an update.
- 03:57 again 3 x 4: the same camera server from 03:20. `usb_cam.py` now loads `scan_bed.py`
  again at every Start (`importlib.reload`; a broken file is reported, the last good one
  stays), so a changed scan_bed.py counts without restarting the camera. This change itself
  needs one restart.
