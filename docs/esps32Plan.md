# ESP32-S3 firmware - how to start, organized for growth

Updated: 2026-11-18
Builds on: `newStatus.md` (hardware confirmed, milestones 0-7) and `drawings/Zortrax M200 project.excalidraw` (camera head, bed sensors, measurement flow).

The goal of this file: start the ESP32-S3 project in a way that still works when the project has a web UI, telnet, BLE, OTA, sensors, a measurement flow and a second ESP32 (the camera head).

---

## 1. Review of the current plan

The current plan is good. The hardware is confirmed, the milestones are small and each one has a clear "done when" test, and the main design rule (one task owns the UART, everything else goes through a queue) is the right one.

Change or add these things before writing code:

| # | Gap | Why it matters | Fix |
|---|-----|----------------|-----|
| 1 | **STOP goes through the normal queue** | If the queue waits for an `ok` behind a long `G28` or `M109`, STOP waits too. | STOP / pause / resume skip the queue and go straight to the UART (`M112`, `M108`, `M410`). Check `M115` for `Cap:EMERGENCY_PARSER:1`. Without it, Marlin only reads `M112` after the current command ends. |
| 2 | **"Wait for ok" has no timeout and no error handling** | Marlin also sends `busy: processing`, `echo:`, `Error:`, `Resend: N`, and auto reports (`T:` from `M155`) that are not answers. A lost `ok` blocks everything forever. | Line protocol with a state machine: `busy:` resets the timeout, `Resend:` resends, timeout means "link lost". When printing from the ESP, use line numbers + checksums (`N12 G1 X10*85`). |
| 3 | **No OTA** | The ESP32 goes inside the printer base (milestone 7). Opening the base for every firmware change is slow. | Partition table with two OTA slots from day one. OTA upload page in milestone 4. |
| 4 | **WiFi credentials have no setup path** | "Credentials in NVS" needs a way to put them there. | Option: SoftAP fallback with a setup page, or BLE provisioning (`network_provisioning`). See section 5. |
| 5 | **Flat `main/` folder** | Fine for 6 files, messy at 20+. The camera and sensors will add a lot. | Put each feature in its own ESP-IDF **component** (section 3). |
| 6 | **No git** | The folder is not a git repo. There's no history and no way back if a change breaks something. | `git init` before the first line of code (section 4). |
| 7 | **Nothing protects the UART pins** | If the printer is ON and the ESP32 is OFF, PA9 drives 3.3 V into an unpowered GPIO18 and back-powers the ESP through its protection diodes. | Add a **1 kΩ series resistor** on both data lines (DEBUG 3 and 4). It costs nothing and protects both chips. |
| 8 | **The file list mentions files that are not here** | `howToWifi.md`, `pinout.md`, `uart_finder/` and `pin_scanner/` are not in `Wifi Enabled/` or the parent folder. | Find them and move them into the new `docs/` and `tools/archive/`, or remove the references. |
| 9 | **The firmware's features aren't checked** | `M155`, `M27 S2`, saving settings and SD support all depend on how this Marlin build was compiled. | Milestone 2 has an extra step: save the full `M115` output (`Cap:` lines) into `docs/marlin_caps.md`. Build on what it reports. |

Small notes:
- 250000 baud is exact on the ESP32-S3 (80 MHz / 250000 = 320). No baud error problem.
- GPIO17/18 are good choices (not strapping, not USB, not used by flash/PSRAM).
- Do milestone 5 (telnet) before milestone 4 (web) if you want an easy test tool early. Telnet takes about 80 lines, and after that you can test everything from the PC.

---

## 2. Decisions to make (options)

### 2.1 Framework

| Option | Pros | Cons |
|--------|------|------|
| **A. ESP-IDF (VS Code extension)** (recommended, already planned) | Full control of tasks, UART, partitions, OTA, NimBLE, and a proper component system for growth. Best documented for the S3. | More code than Arduino for simple things. |
| B. Arduino as an ESP-IDF component | Lets you use Arduino libraries (sensor drivers) inside an IDF project. | Bigger build, version pinning between Arduino core and IDF is fiddly. |
| C. PlatformIO (Arduino or IDF) | One tool for Nano + ESP32, nice library manager. | Lags behind ESP-IDF releases, and an extra layer to debug. |
| D. ESPHome | A web UI and Home Assistant integration with almost no code. | Not suited to a G-code line protocol with ok handling and streaming. |

**Recommendation: A.** Use plain ESP-IDF, and pull in single Arduino-only libraries only if a sensor really needs one. Most sensors you plan (VL53L1X, SHT31, BME280, HX711) already have ESP-IDF drivers in the Espressif component registry.

**Pin the version.** Write the exact ESP-IDF version in `README.md` (e.g. "ESP-IDF v5.x.y") and only upgrade on purpose.

### 2.2 Repository layout

| Option | Description | When it fits |
|--------|-------------|--------------|
| A. Single project, flat `main/` | What `newStatus.md` shows now. | Small one-off projects. |
| B. Single project, features as components | `main/` only starts things, and each feature lives in `components/<name>/`. | One firmware that grows. |
| **C. Monorepo: several firmwares + shared components + docs + tools** (recommended) | Option B inside a repo that also holds the camera firmware, the web UI source, test tools and docs. | This project: the bridge, the camera head later, maybe a sensor board, and one set of docs. |

**Recommendation: C**, with only the bridge firmware filled in at first. Empty folders cost nothing, and moving things later costs a lot.

### 2.3 Web UI delivery

| Option | Pros | Cons |
|--------|------|------|
| **A. Plain HTML/JS/CSS, embedded in the firmware** (`EMBED_FILES`, gzip) (recommended to start) | No build tools, one file to flash, the UI always matches the firmware. | Every UI change needs a reflash (OTA makes that painless). |
| B. Files in a LittleFS partition | The UI can be updated separately, and it can grow bigger. | Two things to flash and keep in sync. |
| C. Framework (Preact/Svelte + Vite), built and gzipped into A or B | A real UI for the height map, the camera stream and file manager. | Node toolchain, build step. |

**Recommendation:** start with A (one `index.html`, vanilla JS). Keep the UI source in `web/` from the start so moving to C later is just adding a build step.

### 2.4 Internal messaging (how modules talk)

| Option | Description |
|--------|-------------|
| A. Direct calls between modules | Simple, but every module ends up including every other one. |
| **B. Command queue in, event bus out** (recommended) | Clients call `printer_send(line, source)`, which puts the line on the queue. The printer task publishes every received line and every state change on an `esp_event` loop. Web, telnet, BLE and logging just subscribe. Adding a client (MQTT, camera, sensor flow) changes nothing else. |
| C. Full message broker (MQTT inside) | Overkill on-device. MQTT may come later as one more *client*. |

### 2.5 WiFi setup / provisioning

| Option | Pros | Cons |
|--------|------|------|
| A. Credentials in `sdkconfig` / menuconfig | Fastest for milestone 3. | Rebuild to change networks, and the password ends up in files (keep it out of git). |
| **B. SoftAP fallback + setup page** (recommended) | If no WiFi is saved or it fails to connect, the ESP opens `M200-Setup` with a form. Works from any phone. | Some extra web code. |
| C. BLE provisioning (Espressif `network_provisioning` + ESP BLE Prov app) | Official, secure. | Needs a specific phone app, and NimBLE is shared with NUS. |

**Recommendation:** A for milestone 3 (credentials in a git-ignored `sdkconfig` / `secrets` file), B before milestone 7.

### 2.6 Printing files (milestone 6 and later)

| Option | How | Notes |
|--------|-----|-------|
| **A. Print from the printer's SD** (`M20` / `M23` / `M24`) (recommended first) | Marlin does the printing, and the ESP only controls and monitors it. | Most robust. A WiFi drop can't ruin a print. |
| B. Upload over WiFi to the printer's SD (`M28` / `M29`) | The ESP writes the file through the UART. | Slow at 250000 baud (roughly 10-20 KB/s at best), so a big file takes many minutes. Fine for small parts. |
| C. Stream G-code from the ESP (OctoPrint-style) | The file lives on ESP flash or an ESP microSD, and the ESP sends lines with `N` + checksum. | Most flexible (needed for the measurement flow anyway), but a stalled ESP means a stalled print. Do this after A is solid. |

The measurement flow (G28 -> G0 -> M400 -> read sensor -> store) is really option C with sensor reads between lines. So build `printer_link` so it can also be driven by a **job runner** and not only by clients typing commands.

### 2.7 Testing strategy

| Level | Tool | What it catches |
|-------|------|-----------------|
| **Host unit tests** | ESP-IDF `linux` target, or plain CMake + Unity on the PC | Marlin reply parser (`T:` / `B:` / `ok` / `Resend:` / `busy:`), checksum, state struct. No hardware needed. |
| **Fake printer** | Nano `fake_marlin.ino` (already designed) or a Python script on a USB-UART | Queue, timeouts, resend, client broadcast, without risking the real printer. |
| **Real printer** | DEBUG header | Final check of each milestone. |

Keep the parser in its own component with **no ESP-IDF includes** so it builds on the PC.

---

## 3. Recommended layout (Option C)

```
m200/                                 git repo root
  README.md                           what this is, how to build, pinned ESP-IDF version
  .gitignore                          build/, sdkconfig, managed_components/, secrets
  docs/
    newStatus.md                      status (moved from Wifi Enabled/)
    esps3Plan.md                      this file
    pinout.md, howToWifi.md
    marlin_caps.md                    saved M115 output (milestone 2)
    protocol.md                       REST + WebSocket message formats
    drawings/*.excalidraw
  firmware/
    shared/components/                used by several firmwares
      app_config/                     settings in NVS (wifi, token, names), defaults, versioning
      net_wifi/                       STA + SoftAP fallback + mDNS
      ota/                            OTA update over HTTP
      log_bus/                        optional: send ESP logs to the web console
    bridge/                           ESP-IDF project: the ESP32-S3 in the printer base
      CMakeLists.txt                  EXTRA_COMPONENT_DIRS = ../shared/components
      sdkconfig.defaults              NimBLE, console, flash size, partition table
      partitions.csv                  nvs, otadata, ota_0, ota_1 (+ littlefs later)
      main/
        main.c                        init only: config -> wifi -> printer -> servers
        idf_component.yml             espressif/mdns, ...
        Kconfig.projbuild             UART pins, baud, hostname as menuconfig options
      components/
        marlin_proto/                 PURE C: line parser, checksum, reply types (host-testable)
        printer_link/                 UART1 driver, TX queue, ok/busy/resend/timeout, emergency path
        printer_state/                temps, position, job %, link status; publishes events
        job_runner/                   later: stream a file / run a measurement sequence
        web_server/                   HTTP, REST /api/*, WebSocket console, OTA endpoint
        telnet_server/                TCP 23
        ble_nus/                      NimBLE Nordic UART Service
        sensors/                      later: ToF, SHT31, load cell drivers behind one interface
      test/                           host unit tests for marlin_proto, printer_state
    camera/                           later: ESP-IDF project for XIAO S3 Sense (MJPEG)
  web/
    bridge-ui/                        index.html, app.js, style.css (embedded by web_server)
  tools/
    fake_marlin/                      Nano sketch (Test B)
    fake_marlin.py                    same idea for a USB-UART adapter
    m200_client.py                    PC script: send G-code over telnet/WebSocket, used by tests
    archive/uart_finder/, pin_scanner/
  hardware/
    wiring.md, buck converter notes, case STL / 3MF
```

### Component rules (these keep it clean as it grows)
1. **`main/` only wires things together.** No feature logic in `main.c`.
2. **Each component has one public header** (`include/<name>.h`) and declares its dependencies in its `CMakeLists.txt` (`REQUIRES` / `PRIV_REQUIRES`). If you need to add a dependency, check first that the design still makes sense.
3. **Dependencies point one way:** clients (`web_server`, `telnet_server`, `ble_nus`, `job_runner`) -> `printer_link` / `printer_state` -> `marlin_proto`. `printer_link` never includes a client.
4. **Hardware numbers live in Kconfig**, not `#define`s: UART number, GPIOs, baud, hostname. A board change then only needs menuconfig.
5. **Secrets never go in git:** WiFi password and API token come from NVS (or a git-ignored file during development).
6. **Every component gets a log tag** (`static const char *TAG = "printer_link";`) so `idf.py monitor` output is easy to filter.
7. **Message formats are written in `docs/protocol.md`** before the web UI uses them (JSON over WebSocket, e.g. `{"t":"state","nozzle":[215,215],"bed":[80,80],"job":61}`).

---

## 4. How to start: first sessions, step by step

### Step 0 - repo (15 min)
1. Create `m200/` (or rename `Wifi Enabled/`) with the folders above. Empty folders get a `.gitkeep`.
2. Move `newStatus.md`, this file and the `.excalidraw` drawings into `docs/`.
3. `git init`, add `.gitignore`, first commit "project skeleton". Optional: a private GitHub repo as backup.

### Step 1 - milestone 1, ESP-IDF setup
1. Install the ESP-IDF extension and the latest stable ESP-IDF (as in `newStatus.md`). Write the version in `README.md`.
2. Create the project at `firmware/bridge/` (template `sample_project`), target `esp32s3`.
3. Add `partitions.csv` with two OTA slots **now**, and set it in `sdkconfig.defaults`. Changing it later means erasing flash.
4. Flash `hello_world`-style `main.c` that prints the firmware version (`esp_app_get_description()`).
5. Commit. Done when it prints on the monitor.

### Step 2 - milestone 2, UART bridge (components, not `main.c`)
1. `marlin_proto`: parse one line into `{OK, BUSY, ERROR, RESEND n, TEMP_REPORT, ECHO, OTHER}`, plus checksum. Write the host tests first. It's small and easy to test.
2. `printer_link`: UART1 @ 250000 on GPIO17/18 (from Kconfig), RX task that splits lines -> `marlin_proto` -> events, TX task with the queue + ok/timeout state machine, and `printer_emergency()` that writes straight to the UART.
3. Temporary bridge: lines typed in the USB monitor go into the queue, and printer lines get printed.
4. Test against `fake_marlin` first, then the real printer: `M115` -> save the `Cap:` lines to `docs/marlin_caps.md`. Try `M500` (open point from `newStatus.md`).
5. Commit.

### Step 3 onwards
Follow the milestones in `newStatus.md` with these changes:
- 3: `net_wifi` + `app_config` in `firmware/shared/components/`.
- 3b (new): `telnet_server`. It's a cheap test tool for everything after this.
- 4: `web_server` + `web/bridge-ui/` + **OTA upload endpoint**. After this, flash over WiFi.
- 5: `ble_nus`.
- 6: SD printing (option 2.6 A).
- 7: install, token, SoftAP setup.
- 8 (new): `sensors/` + `job_runner` -> measurement flow (project drawing section 5).
- 9 (new): `firmware/camera/` for the camera head, reusing `shared/components`.

---

## 5. Summary of recommendations

| Topic | Choice |
|-------|--------|
| Framework | ESP-IDF, version pinned in README |
| Layout | Monorepo: `firmware/bridge` + `firmware/shared/components` + `web/` + `tools/` + `docs/` |
| Code structure | One component per feature, `main/` only wires them, Kconfig for pins |
| Messaging | Command queue into `printer_link`, `esp_event` bus out to all clients |
| STOP | Emergency path that bypasses the queue |
| Line protocol | ok / busy / Resend / timeout state machine, N + checksum when streaming |
| Flash layout | Two OTA slots from step 1 |
| Web UI | Plain embedded HTML/JS first, source in `web/` |
| WiFi setup | menuconfig/secret file now, SoftAP fallback before install |
| Printing | From the printer's SD first, ESP streaming later (needed for measurements) |
| Tests | Host tests for the parser, fake Marlin for the link, real printer last |
| Safety | 1 kΩ series resistors on DEBUG 3 and 4 |
| Version control | `git init` before the first code |
