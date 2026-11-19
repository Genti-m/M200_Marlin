# Zortrax M200 WiFi bridge

ESP32-S3 on the M200 V4 DEBUG header (UART1, 250000 8N1) that adds a web interface (and later telnet and BLE) to the Zortrax Marlin fork. The printer is being converted into a laser engraver: [firmware/bridge/road_Change.md](firmware/bridge/road_Change.md).

- Status and milestones: [docs/newStatus.md](docs/newStatus.md)
- Structure and decisions: [docs/esps32Plan.md](docs/esps32Plan.md)

## Layout

| Folder | Contents |
|--------|----------|
| `docs/` | status, plan, pinouts, saved `M115` caps, protocol, `drawings/` |
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
- PC unit tests (needs gcc or clang): `sh tools/run_host_tests.sh`. CI (`.github/workflows/ci.yml`) runs them and the firmware build on every push.
- UI without the printer: `python tools/mock_bridge.py` -> http://localhost:8080
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
