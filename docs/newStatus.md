# Zortrax M200 WiFi bridge - status and plan

Updated: 2026-11-19
Drawing: `drawings/Status and plan.excalidraw`

## Where we are

| Item | Status |
|------|--------|
| Printer | Zortrax M200 **V4**, Zortrax Marlin fork (`bugfix-2.1.x`, compiled Aug 13 2025) |
| Serial port | **DEBUG header**, Marlin `SERIAL_PORT 1` (USART1), **250000 baud, 8N1** |
| DEBUG pin 3 | **TX** of the printer (PA9, chip pin 68): **confirmed**, Marlin boot text received |
| DEBUG pin 4 | **RX** of the printer (PA10, chip pin 69): confirmed by continuity |
| DEBUG pin 7 | **GND**: confirmed |
| DEBUG pin 1 | 3.3 V: **do not use** (don't power the ESP32 from it) |
| DEBUG pins 2, 5, 6, 8 | BOOT0, SWD, RESET: **never connect** |

All the hardware facts are confirmed. Next comes the ESP32-S3 firmware.

## Wiring (printer OFF while connecting)

| DEBUG pin | ESP32-S3 |
|-----------|----------|
| 3 (printer TX) | GPIO18 (RX), via 1 kΩ |
| 4 (printer RX) | GPIO17 (TX), via 1 kΩ |
| 7 (GND) | GND |

(Same table as the README; GPIOs are Kconfig `PRINTER_LINK_TX_GPIO` / `_RX_GPIO`.)

During development, power the ESP32-S3 from the PC's USB. Later use a 24 V -> 5 V buck converter.
The Arduino Nano was only for tests. **Never connect the Nano to pin 4**: it would drive 5 V into the STM32.

## How we continue: ESP-IDF in VS Code

### Setup (once)
1. VS Code -> Extensions -> install **ESP-IDF** (by Espressif).
2. Command palette: **ESP-IDF: Configure ESP-IDF Extension** -> Express -> pick the latest stable ESP-IDF release -> Install.
3. **ESP-IDF: New Project** (template `sample_project`), name `m200_bridge`.
4. **ESP-IDF: Set Espressif Device Target** -> `esp32s3`.
5. Select the COM port, then **ESP-IDF: Build, Flash and Monitor** (also in the status bar).
   - If you use the board's "USB" port (not "UART"), set `Component config -> ESP System Settings -> Channel for console output` to **USB Serial/JTAG** in menuconfig.

### Project layout (as built)
```
firmware/bridge/                 ESP-IDF project (m200_bridge)
  main/main.c                    start-up only: NVS, netif, starts the components, OTA confirm
  main/console_bridge.c          USB console: G-code + "!" bridge commands (!wifi, !token, !homexy, ...)
  components/marlin_proto/       pure C: parse Marlin replies (ok, busy, Resend, T:/B:) + PC test
  components/gcode_guard/        pure C: laser-build G-code rules + axis tracking + PC test
  components/printer_link/       owns UART1: queue, one line in flight, waits for "ok"
  components/printer_state/      state, history, settings (NVS), bridge homing tasks
  components/web_server/         HTTP, WebSocket /ws, REST /api/*, OTA, API token
  sdkconfig.defaults, partitions.csv (2 OTA slots + littlefs)
firmware/shared/components/wifi_link/   WiFi station + mDNS (m200.local), credentials in NVS
web/bridge-ui/                   index.html / style.css / app.js, gzipped into the firmware
tools/mock_bridge.py             fake bridge + Marlin on the PC, same protocol
tools/run_host_tests.sh          PC unit tests (also run by .github/workflows/ci.yml)
```

### Design rules
- **One task owns the UART.** WiFi, telnet and BLE put commands in a FreeRTOS queue; the printer task sends one line, waits for `ok`, then sends the next.
- Every printer reply is **broadcast** to all clients (web, telnet, BLE).
- On connect send `M155 S2`: Marlin then reports temperatures every 2 s by itself (no polling).
- Parse `T:` / `B:` (temps), `M27` (SD progress), `M114` (position) into a small state struct for the web interface.

### Milestones
| # | Goal | Done when |
|---|------|-----------|
| 0 | Find TX/RX/GND | **done** (DEBUG 3/4/7, 250000) |
| 1 | ESP-IDF setup | **done** 2026-11-18 (IDF v6.1.1, flashed over native USB COM7, heartbeat on monitor) |
| 2 | UART bridge | **done** 2026-11-18 (`M115` answered both ways, caps in `marlin_caps.md`) |
| 3 | WiFi + mDNS | **code done** 2026-11-18, builds; test on hardware: `ping m200.local` works, credentials stored in NVS |
| 4 | **Web interface** | **code done** 2026-11-18, builds; test on hardware: `http://m200.local`: temps, Home / Motors off / Preheat / STOP buttons, live console |
| 5 | Telnet + BLE (not started) | `telnet m200.local 23` and nRF Connect both send `M105` and see the reply |
| 6 | Printing from SD | file list (`M20`), start (`M23` + `M24`), pause/resume, progress bar (`M27`) |
| 7 | Install | buck converter, case inside the base. Access token on the web API: **code done** 2026-11-19 (`!token`, see `protocol.md`) |

## Milestones 3 + 4: first test on the hardware

1. Build, Flash and Monitor (USB, COM7) as before. The new `sdkconfig` was regenerated from `sdkconfig.defaults`.
2. In the monitor type `!wifi <ssid> <password>` (SSID in "quotes" if it has spaces). Stored in NVS, survives reflashing.
   Wait for `connected, IP 192.168.x.y -> http://m200.local`. `!net` shows the status at any time, `!help` lists the commands.
3. PC: `ping m200.local`, then open `http://m200.local` (if `.local` doesn't resolve on Windows, use the IP).
4. Printer on: temps appear in about 2 s (`M155 S2` is sent at boot and after every printer reset).
5. Check: Home -> "homed" badge, jog 1 mm, Set/Off on hotend and bed, console quick buttons, STOP (tap twice).
6. Set an API token: `!token <secret>` (OTA is refused without one). From then on firmware updates can go over WiFi:
   `curl -H "X-Token: <secret>" --data-binary @build/m200_bridge.bin http://m200.local/api/ota`.
   A new image that gets no WiFi within 120 s is rolled back (needs the rollback bootloader: one USB flash after a fresh `sdkconfig`, see `road_Change.md` ch. 7).

UI changes without the printer: `python tools/mock_bridge.py` -> http://localhost:8080 (same protocol, fake Marlin).

## Hardware test checklist (road_Change.md ch. 5-7: built, not flashed yet)

Tick each one on the printer, in this order. Printer on, nothing on the bed.

**Flash (USB, once)**
- [ ] Delete `firmware/bridge/sdkconfig`, Build, Flash over USB (COM7). Boot log shows the new version, no errors
- [ ] `!net`: WiFi connected. `http://m200.local` loads, the icons show, the dots go green

**Token and OTA**
- [ ] `!token <secret>` (8..64 chars). The open page asks for it on the next command; a wrong one is refused
- [ ] STOP works on a page that is *not* logged in
- [ ] `curl -X POST http://m200.local/api/ota` without `X-Token` -> 401
- [ ] OTA with the token -> reboots, log shows `new firmware confirmed (WiFi up)`
- [ ] Rollback: build a test image with `vTaskDelay(pdMS_TO_TICKS(5000)); abort();` at the end of
      `app_main`, OTA it. It crashes before WiFi confirms it, and the board must boot the previous
      image by itself. (WiFi credentials live in NVS and survive OTA, so "no WiFi" is harder to fake.)

**Homing and limits**
- [ ] `M119` with nothing pressed: all `open`. Press each switch by hand: `TRIGGERED`
- [ ] Home XY: X then Y move *toward* their switches, stop, back off, park at 10/10
- [ ] Home Z: steps toward the switch (check `z_dir`; wrong way -> STOP, flip it in Setting tweaking)
- [ ] Jog 10 mm each way; a jog past 170 is refused with an `Error:bridge:` line
- [ ] A plain move into an endstop with `M120` on stops there, and Marlin keeps answering
- [ ] `G28`, `M104 S200`, `M104S200`, `N5G28` from the console: all refused
- [ ] STOP during a Z home: home aborts, `RESTART` needed, axes unhomed

## Open points (small)
- ~~`Hardcoded Default Settings Loaded`: try `M500`~~ -> `Cap:EEPROM:0`, this build can't save settings (see `marlin_caps.md`).
- `Cap:EMERGENCY_PARSER:0`: STOP (`M112`) only runs after the current command. Marlin 2.x sends busy as `echo:busy: processing`; `marlin_proto` now treats that as BUSY (it was ECHO, so the `ok` timeout never restarted). Confirm during a real `G28`.
- Pick the buck converter after measuring the PSU voltage (24 V expected).
- API token done (web + REST, `!token`). Telnet, when it comes (milestone 5), must use it too. Still home network only: no TLS.

## Reference files
- `howToWifi.md`: full background, the Arduino version of the bridge, BLE usage.
- `pinout.md`: all measurements and pinouts.
- `uart_finder/`, `pin_scanner/`: Nano test sketches (not needed anymore).
