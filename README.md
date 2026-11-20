# Zortrax M200 WiFi bridge

ESP32-S3 on the M200 V4 DEBUG header (UART1, 250000 8N1) that adds a web interface (and later telnet and BLE) to the Zortrax Marlin fork. The printer is being converted into a laser engraver: [firmware/bridge/road_Change.md](firmware/bridge/road_Change.md).

- Status and milestones: [docs/newStatus.md](docs/newStatus.md)
- Structure and decisions: [docs/esps32Plan.md](docs/esps32Plan.md)

## Layout

| Folder | Contents |
|--------|----------|
| `docs/` | status (with the DEBUG header pinout), plan, saved `M115` caps, protocol, `drawings/` |
| `firmware/bridge/` | ESP-IDF project for the ESP32-S3 in the printer base (created in milestone 1) |
| `firmware/shared/components/` | components used by several firmwares (config, WiFi, OTA) |
| `firmware/camera/` | later: camera head firmware |
| `web/bridge-ui/` | web interface source, embedded into the bridge firmware |
| `tools/` | `mock_bridge.py` (fake bridge + Marlin), `run_host_tests.sh`, wiring drawing generators, `archive/` for old Nano test sketches |
| `hardware/` | wiring, buck converter, case |

## Build

- ESP-IDF version: **v6.1.1** (pinned, upgrade on purpose), installed by EIM at `C:\esp\v6.1.1\esp-idf`
- Target: `esp32s3`
- Open `m200.code-workspace` (or `firmware/bridge/`) in VS Code with the ESP-IDF extension -> Build, Flash and Monitor.
- PC unit tests (needs gcc or clang): `sh tools/run_host_tests.sh` (Git Bash). On Windows install MSYS2 (`winget install MSYS2.MSYS2`, then `pacman -S mingw-w64-ucrt-x86_64-gcc` in its shell): the script finds `C:\msys64\ucrt64\bin\gcc` by itself. ESP-IDF's own compilers only build for the chip. CI (`.github/workflows/ci.yml`) runs the tests (with ASan/UBSan) and the firmware build on every push.
- UI without the printer: `python tools/mock_bridge.py` -> http://localhost:8080
- USB camera on this PC for the page's Camera row: `pip install opencv-python`, then
  `python tools/usb_cam.py --list` and `python tools/usb_cam.py --cam N` -> put
  `http://<this-pc>:8081/stream` in Setting tweaking -> Camera. The bridge never sees the camera.
  Close the camera maker's app first (it holds the camera). The sliders button in the Camera
  row changes the camera's own settings (brightness, exposure, white balance, focus, zoom...).
- Bed scan (camera on the head): `python tools/scan_bed.py --check`, then `python tools/scan_bed.py`
  -> tiles + `collage.jpg` in `scans/<date_time>/` (`--dry-run` first; see the script's top).
  Or from the page: the Scan and Collages buttons in the Camera row (with `usb_cam.py` as the camera).
- OTA: set a token once on the USB console (`!token <secret>`), then
  `curl -H "X-Token: <secret>" --data-binary @firmware/bridge/build/m200_bridge.bin http://m200.local/api/ota`.
  Protocol and authentication: [docs/protocol.md](docs/protocol.md).

## Wiring

| DEBUG pin | ESP32-S3 |
|-----------|----------|
| 3 (printer TX) | GPIO18 (RX), via 1 kΩ |
| 4 (printer RX) | GPIO17 (TX), via 1 kΩ |
| 7 (GND) | GND |

Never connect DEBUG pins 1, 2, 5, 6, 8.
