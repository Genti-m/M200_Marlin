// API token: kept in NVS (namespace "bridge", key "api_token"), set from the USB console.
#include <string.h>

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "nvs.h"
#include "api_token.h"
#include "web_server.h"

static const char *TAG = "api_token";

#define NVS_NS  "bridge"
#define NVS_KEY "api_token"

static char s_token[WEB_TOKEN_MAX + 1];
static uint32_t s_gen = 1;  // +1 on every change: older WebSocket logins no longer count
static portMUX_TYPE s_lock = portMUX_INITIALIZER_UNLOCKED;

void web_token_load(void)
{
    char t[WEB_TOKEN_MAX + 1] = {0};
    nvs_handle_t h;
    if (nvs_open(NVS_NS, NVS_READONLY, &h) == ESP_OK) {
        size_t len = sizeof(t);
        if (nvs_get_str(h, NVS_KEY, t, &len) != ESP_OK) {
            t[0] = '\0';
        }
        nvs_close(h);
    }
    portENTER_CRITICAL(&s_lock);
    strlcpy(s_token, t, sizeof(s_token));
    portEXIT_CRITICAL(&s_lock);
    if (t[0]) {
        ESP_LOGI(TAG, "API token set: web control and OTA need it");
    } else {
        ESP_LOGW(TAG, "no API token: web control is open, OTA is refused (set one: !token <secret>)");
    }
}

esp_err_t web_server_set_token(const char *token)
{
    size_t n = token ? strlen(token) : 0;
    if (n > 0 && (n < WEB_TOKEN_MIN || n > WEB_TOKEN_MAX)) {
        return ESP_ERR_INVALID_ARG;
    }
    for (size_t i = 0; i < n; i++) {
        if (token[i] <= ' ' || token[i] > '~') {  // printable ASCII, no spaces: fits a header
            return ESP_ERR_INVALID_ARG;
        }
    }
    nvs_handle_t h;
    esp_err_t err = nvs_open(NVS_NS, NVS_READWRITE, &h);
    if (err != ESP_OK) {
        return err;
    }
    err = n ? nvs_set_str(h, NVS_KEY, token) : nvs_erase_key(h, NVS_KEY);
    if (err == ESP_ERR_NVS_NOT_FOUND) {
        err = ESP_OK;  // clearing a token that wasn't set
    }
    if (err == ESP_OK) {
        err = nvs_commit(h);
    }
    nvs_close(h);
    if (err == ESP_OK) {
        portENTER_CRITICAL(&s_lock);
        strlcpy(s_token, n ? token : "", sizeof(s_token));
        s_gen++;
        portEXIT_CRITICAL(&s_lock);
    }
    return err;
}

bool web_server_token_is_set(void)
{
    portENTER_CRITICAL(&s_lock);
    bool set = s_token[0] != '\0';
    portEXIT_CRITICAL(&s_lock);
    return set;
}

bool web_token_check(const char *given)
{
    char t[WEB_TOKEN_MAX + 1];
    portENTER_CRITICAL(&s_lock);
    memcpy(t, s_token, sizeof(t));
    portEXIT_CRITICAL(&s_lock);
    if (!t[0] || !given) {
        return false;
    }
    // Constant time over the stored token's length: no early exit on the first wrong char.
    size_t tl = strlen(t), gl = strnlen(given, WEB_TOKEN_MAX + 1);
    unsigned diff = (unsigned)(tl ^ gl);
    for (size_t i = 0; i < tl; i++) {
        diff |= (unsigned char)t[i] ^ (unsigned char)(i < gl ? given[i] : 0);
    }
    return diff == 0;
}

uint32_t web_token_generation(void)
{
    portENTER_CRITICAL(&s_lock);
    uint32_t g = s_gen;
    portEXIT_CRITICAL(&s_lock);
    return g;
}
