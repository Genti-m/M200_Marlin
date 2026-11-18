# Zortrax M200 WiFi bridge

ESP32-S3 on the M200 V4 DEBUG header (UART1, 250000 8N1) that adds a web interface, telnet and BLE to the Zortrax Marlin fork.

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
| `tools/` | fake Marlin, PC test client, `archive/` for old Nano test sketches |
| `hardware/` | wiring, buck converter, case |

## Build

- ESP-IDF version: **v6.1.1** (pinned, upgrade on purpose), installed by EIM at `C:\esp\v6.1.1\esp-idf`
- Target: `esp32s3`
- Open `firmware/bridge/` in VS Code with the ESP-IDF extension -> Build, Flash and Monitor.

## Wiring

| DEBUG pin | ESP32-S3 |
|-----------|----------|
| 3 (printer TX) | GPIO18 (RX), via 1 kΩ |
| 4 (printer RX) | GPIO17 (TX), via 1 kΩ |
| 7 (GND) | GND |

Never connect DEBUG pins 1, 2, 5, 6, 8.
