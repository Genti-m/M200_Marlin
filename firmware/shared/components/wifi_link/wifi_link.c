#include "wifi_link.h"

#include <stdio.h>
#include <string.h>

#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/timers.h"
#include "mdns.h"
#include "nvs.h"
#include "sdkconfig.h"

static const char *TAG = "wifi_link";

#define NVS_NS        "wifi"
#define RETRY_MIN_MS  1000
#define RETRY_MAX_MS  30000

static esp_netif_t *s_netif;
static TimerHandle_t s_retry_timer;
static volatile bool s_connected;
static volatile bool s_have_creds;
static int s_retry;
static esp_ip4_addr_t s_ip;
static char s_ssid[33];
static volatile int s_last_reason;  // last WIFI_REASON_*, 0 = none since the last connect

const char *wifi_link_reason_str(int reason)
{
    switch (reason) {
    case 0:                                              return "none";
    case WIFI_REASON_AUTH_EXPIRE:                        return "auth expired (wrong password?)";
    case WIFI_REASON_AUTH_FAIL:                          return "auth failed (wrong password?)";
    case WIFI_REASON_4WAY_HANDSHAKE_TIMEOUT:
    case WIFI_REASON_HANDSHAKE_TIMEOUT:                  return "handshake timeout (wrong password?)";
    case WIFI_REASON_ASSOC_FAIL:                         return "association failed";
    case WIFI_REASON_ASSOC_TOOMANY:                      return "router has too many clients";
    case WIFI_REASON_NO_AP_FOUND:                        return "network not found (name / 2.4 GHz / signal)";
    case WIFI_REASON_NO_AP_FOUND_W_COMPATIBLE_SECURITY:  return "network found, security mode not supported";
    case WIFI_REASON_NO_AP_FOUND_IN_AUTHMODE_THRESHOLD:  return "network found, security weaker than WPA2";
    case WIFI_REASON_NO_AP_FOUND_IN_RSSI_THRESHOLD:      return "network found, signal too weak";
    case WIFI_REASON_BEACON_TIMEOUT:                     return "lost the router (signal)";
    case WIFI_REASON_CONNECTION_FAIL:                    return "connection failed";
    default:                                             return "see ESP-IDF wifi_err_reason_t";
    }
}

static esp_err_t save_creds(const char *ssid, const char *pass)
{
    nvs_handle_t h;
    esp_err_t err = nvs_open(NVS_NS, NVS_READWRITE, &h);
    if (err != ESP_OK) {
        return err;
    }
    err = nvs_set_str(h, "ssid", ssid);
    if (err == ESP_OK) {
        err = nvs_set_str(h, "pass", pass);
    }
    if (err == ESP_OK) {
        err = nvs_commit(h);
    }
    nvs_close(h);
    return err;
}

// Fill cfg->sta from NVS, else from Kconfig (and then save to NVS).
static bool load_creds(wifi_config_t *cfg)
{
    memset(cfg, 0, sizeof(*cfg));
    nvs_handle_t h;
    if (nvs_open(NVS_NS, NVS_READONLY, &h) == ESP_OK) {
        size_t len = sizeof(cfg->sta.ssid);
        esp_err_t err = nvs_get_str(h, "ssid", (char *)cfg->sta.ssid, &len);
        len = sizeof(cfg->sta.password);
        if (err == ESP_OK && nvs_get_str(h, "pass", (char *)cfg->sta.password, &len) != ESP_OK) {
            cfg->sta.password[0] = '\0';
        }
        nvs_close(h);
        if (err == ESP_OK) {
            return cfg->sta.ssid[0] != '\0';  // "" = cleared on purpose: no Kconfig fallback
        }
    }
    if (CONFIG_WIFI_LINK_DEFAULT_SSID[0]) {
        strlcpy((char *)cfg->sta.ssid, CONFIG_WIFI_LINK_DEFAULT_SSID, sizeof(cfg->sta.ssid));
        strlcpy((char *)cfg->sta.password, CONFIG_WIFI_LINK_DEFAULT_PASSWORD, sizeof(cfg->sta.password));
        if (save_creds(CONFIG_WIFI_LINK_DEFAULT_SSID, CONFIG_WIFI_LINK_DEFAULT_PASSWORD) == ESP_OK) {
            ESP_LOGI(TAG, "Kconfig WiFi credentials saved to NVS");
        }
        return true;
    }
    return false;
}

static esp_err_t apply_config(wifi_config_t *cfg)
{
    cfg->sta.threshold.authmode = cfg->sta.password[0] ? WIFI_AUTH_WPA2_PSK : WIFI_AUTH_OPEN;
    cfg->sta.sae_pwe_h2e = WPA3_SAE_PWE_BOTH;
    cfg->sta.scan_method = WIFI_ALL_CHANNEL_SCAN;          // pick the strongest AP of a mesh
    cfg->sta.sort_method = WIFI_CONNECT_AP_BY_SIGNAL;
    esp_err_t err = esp_wifi_set_config(WIFI_IF_STA, cfg);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "esp_wifi_set_config: %s", esp_err_to_name(err));
        return err;
    }
    strlcpy(s_ssid, (const char *)cfg->sta.ssid, sizeof(s_ssid));
    return ESP_OK;
}

static void retry_cb(TimerHandle_t t)
{
    if (s_have_creds && !s_connected) {
        esp_wifi_connect();
    }
}

static void on_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        if (s_have_creds) {
            ESP_LOGI(TAG, "connecting to \"%s\"", s_ssid);
            esp_wifi_connect();
        } else {
            ESP_LOGW(TAG, "no WiFi credentials: type  !wifi <ssid> <password>  in the USB console");
        }
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        const wifi_event_sta_disconnected_t *ev = data;
        bool was_connected = s_connected;
        s_connected = false;
        s_last_reason = ev->reason;
        if (!s_have_creds) {
            return;
        }
        int delay = RETRY_MIN_MS << (s_retry < 5 ? s_retry : 5);
        if (delay > RETRY_MAX_MS) {
            delay = RETRY_MAX_MS;
        }
        s_retry++;
        if (was_connected || s_retry <= 3 || s_retry % 10 == 0) {
            ESP_LOGW(TAG, "disconnected from \"%s\" (reason %d: %s), retry in %d s", s_ssid,
                     ev->reason, wifi_link_reason_str(ev->reason), delay / 1000);
        }
        xTimerChangePeriod(s_retry_timer, pdMS_TO_TICKS(delay), 0);
        xTimerStart(s_retry_timer, 0);
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        const ip_event_got_ip_t *ev = data;
        s_ip = ev->ip_info.ip;
        s_connected = true;
        s_retry = 0;
        s_last_reason = 0;
        ESP_LOGI(TAG, "connected, IP " IPSTR "  ->  http://%s.local  or  http://" IPSTR,
                 IP2STR(&s_ip), CONFIG_WIFI_LINK_HOSTNAME, IP2STR(&s_ip));
    }
}

static void start_mdns(void)
{
    esp_err_t err = mdns_init();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "mdns_init: %s", esp_err_to_name(err));
        return;
    }
    mdns_hostname_set(CONFIG_WIFI_LINK_HOSTNAME);
    mdns_instance_name_set("Zortrax M200 bridge");
    mdns_service_add(NULL, "_http", "_tcp", 80, NULL, 0);
}

esp_err_t wifi_link_start(void)
{
    if (s_netif) {
        return ESP_ERR_INVALID_STATE;
    }
    s_netif = esp_netif_create_default_wifi_sta();
    esp_netif_set_hostname(s_netif, CONFIG_WIFI_LINK_HOSTNAME);

    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    esp_err_t err = esp_wifi_init(&init);
    if (err != ESP_OK) {
        return err;
    }
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, on_event, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, on_event, NULL));
    s_retry_timer = xTimerCreate("wifi_retry", pdMS_TO_TICKS(RETRY_MIN_MS), pdFALSE, NULL, retry_cb);

    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));  // our own NVS namespace holds them
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    wifi_config_t cfg;
    s_have_creds = load_creds(&cfg);
    if (s_have_creds && apply_config(&cfg) != ESP_OK) {
        s_have_creds = false;
    }
    ESP_ERROR_CHECK(esp_wifi_start());
    esp_wifi_set_ps(WIFI_PS_NONE);  // powered by the printer: answer fast instead of saving power

    start_mdns();
    return ESP_OK;
}

esp_err_t wifi_link_set_credentials(const char *ssid, const char *pass)
{
    if (!s_netif || !ssid || !ssid[0] || strlen(ssid) > 32 || (pass && strlen(pass) > 63)) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!pass) {
        pass = "";
    }
    esp_err_t err = save_creds(ssid, pass);
    if (err != ESP_OK) {
        return err;
    }
    wifi_config_t cfg = {0};
    strlcpy((char *)cfg.sta.ssid, ssid, sizeof(cfg.sta.ssid));
    strlcpy((char *)cfg.sta.password, pass, sizeof(cfg.sta.password));
    s_retry = 0;
    bool was_connected = s_connected;
    if (was_connected) {
        esp_wifi_disconnect();  // the DISCONNECTED event schedules the reconnect
    }
    err = apply_config(&cfg);
    if (err != ESP_OK) {
        return err;
    }
    s_have_creds = true;
    ESP_LOGI(TAG, "credentials for \"%s\" saved, connecting", ssid);
    if (!was_connected) {
        esp_wifi_connect();
    }
    return ESP_OK;
}

esp_err_t wifi_link_clear_credentials(void)
{
    if (!s_netif) {
        return ESP_ERR_INVALID_STATE;
    }
    esp_err_t err = save_creds("", "");  // keep an empty entry so the Kconfig defaults stay off
    if (err != ESP_OK) {
        return err;
    }
    s_have_creds = false;  // the DISCONNECTED event sees this and does not retry
    xTimerStop(s_retry_timer, 0);
    s_retry = 0;
    esp_wifi_disconnect();
    wifi_config_t cfg = {0};
    esp_wifi_set_config(WIFI_IF_STA, &cfg);
    s_ssid[0] = '\0';
    ESP_LOGI(TAG, "WiFi credentials cleared");
    return ESP_OK;
}

esp_err_t wifi_link_scan(wifi_ap_record_t *aps, uint16_t *count)
{
    if (!s_netif) {
        return ESP_ERR_INVALID_STATE;
    }
    wifi_scan_config_t sc = {.show_hidden = true};
    esp_err_t err = esp_wifi_scan_start(&sc, true);  // blocks ~2-3 s
    if (err != ESP_OK) {
        return err;  // ESP_ERR_WIFI_STATE while a connect attempt is running
    }
    return esp_wifi_scan_get_ap_records(count, aps);
}

int wifi_link_last_reason(void)
{
    return s_last_reason;
}

bool wifi_link_connected(void)
{
    return s_connected;
}

int wifi_link_rssi(void)
{
    wifi_ap_record_t ap;
    if (!s_connected || esp_wifi_sta_get_ap_info(&ap) != ESP_OK) {
        return 0;
    }
    return ap.rssi;
}

void wifi_link_ip_str(char *buf, size_t size)
{
    if (!s_connected) {
        buf[0] = '\0';
        return;
    }
    snprintf(buf, size, IPSTR, IP2STR(&s_ip));
}

void wifi_link_ssid(char *buf, size_t size)
{
    strlcpy(buf, s_ssid, size);
}

const char *wifi_link_hostname(void)
{
    return CONFIG_WIFI_LINK_HOSTNAME;
}
