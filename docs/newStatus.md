# Zortrax M200 WiFi bridge - status and plan

Updated: 2026-11-18
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
| 3 (printer TX) | GPIO18 (RX) |
| 4 (printer RX) | GPIO17 (TX) |
| 7 (GND) | GND |

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

### Project layout (planned)
```
m200_bridge/
  main/
    main.c           start-up, creates the tasks
    printer_uart.c   UART1 @ 250000, line reader, command queue, "ok" handling
    wifi.c           WiFi station + mDNS (m200.local), credentials in NVS
    web.c            HTTP server: web interface, REST (/api/...), WebSocket console
    telnet.c         TCP port 23 raw console
    ble_nus.c        NimBLE Nordic UART Service (phone)
    www/index.html   web interface, embedded in the firmware
  main/idf_component.yml   espressif/mdns
  sdkconfig.defaults       NimBLE on, console settings
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
| 3 | WiFi + mDNS | `ping m200.local` works, credentials stored in NVS |
| 4 | **Web interface** | `http://m200.local`: temps, Home / Motors off / Preheat / STOP buttons, live console |
| 5 | Telnet + BLE | `telnet m200.local 23` and nRF Connect both send `M105` and see the reply |
| 6 | Printing from SD | file list (`M20`), start (`M23` + `M24`), pause/resume, progress bar (`M27`) |
| 7 | Install | buck converter, case inside the base, access token on the web API |

## Open points (small)
- ~~`Hardcoded Default Settings Loaded`: try `M500`~~ -> `Cap:EEPROM:0`, this build can't save settings (see `marlin_caps.md`).
- `Cap:EMERGENCY_PARSER:0`: STOP (`M112`) only runs after the current command. Check during a `G28` whether `busy:` lines appear (sets the `ok` timeout).
- Pick the buck converter after measuring the PSU voltage (24 V expected).
- The web page and telnet have no password at first: home network only, add a token in milestone 7.

## Reference files
- `howToWifi.md`: full background, the Arduino version of the bridge, BLE usage.
- `pinout.md`: all measurements and pinouts.
- `uart_finder/`, `pin_scanner/`: Nano test sketches (not needed anymore).
