// web_server: HTTP on port 80. Serves the embedded web UI (web/bridge-ui), the WebSocket /ws
// and REST /api/* described in docs/protocol.md, and OTA firmware upload (POST /api/ota).
//
// API token (NVS): with one set, every write (REST: header "X-Token", WebSocket: an "auth"
// message first) needs it. Without one, web control is open but OTA is refused. STOP never
// needs the token. Reading the state, settings and console history is always open.
#pragma once

#include <stdbool.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define WEB_TOKEN_MIN 8
#define WEB_TOKEN_MAX 64

// After printer_link_start() and printer_state_start(). Works before WiFi is up.
esp_err_t web_server_start(void);

// Save the API token in NVS ("" or NULL clears it). WEB_TOKEN_MIN..MAX printable ASCII chars,
// no spaces: else ESP_ERR_INVALID_ARG. Applies at once, and logs out every open WebSocket.
esp_err_t web_server_set_token(const char *token);
bool web_server_token_is_set(void);

#ifdef __cplusplus
}
#endif
