// web_server: HTTP on port 80. Serves the embedded web UI (web/bridge-ui), the WebSocket /ws
// and REST /api/* described in docs/protocol.md, and OTA firmware upload (POST /api/ota).
#pragma once

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

// After printer_link_start() and printer_state_start(). Works before WiFi is up.
esp_err_t web_server_start(void);

#ifdef __cplusplus
}
#endif
