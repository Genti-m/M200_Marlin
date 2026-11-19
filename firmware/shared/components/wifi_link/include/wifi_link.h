// wifi_link: WiFi station + mDNS (<hostname>.local).
//
// Credentials come from NVS (namespace "wifi"); if NVS is empty, from the Kconfig defaults,
// which are then saved to NVS. Reconnects by itself with a growing delay (1 s ... 30 s).
// Before wifi_link_start(): nvs_flash_init(), esp_netif_init(), esp_event_loop_create_default().
#pragma once

#include <stdbool.h>
#include <stddef.h>

#include "esp_err.h"
#include "esp_wifi_types.h"

#ifdef __cplusplus
extern "C" {
#endif

esp_err_t wifi_link_start(void);

// Save new credentials to NVS and reconnect with them. pass may be "" (open network).
esp_err_t wifi_link_set_credentials(const char *ssid, const char *pass);

// Forget the credentials (NVS too) and disconnect. Stays offline, also after a reboot and even
// with Kconfig defaults set, until wifi_link_set_credentials() is called again.
esp_err_t wifi_link_clear_credentials(void);

// Scan for access points (blocks ~2-3 s). count: in = size of aps, out = records filled.
// ESP_ERR_WIFI_STATE if a connect attempt is in progress: try again a moment later.
esp_err_t wifi_link_scan(wifi_ap_record_t *aps, uint16_t *count);

// Last disconnect reason (WIFI_REASON_*), 0 if none since the last successful connect.
int wifi_link_last_reason(void);
const char *wifi_link_reason_str(int reason);

bool wifi_link_connected(void);

// Signal of the access point in dBm, 0 if not connected.
int wifi_link_rssi(void);

// "192.168.1.23", or "" if not connected.
void wifi_link_ip_str(char *buf, size_t size);

// SSID in use ("" if none configured).
void wifi_link_ssid(char *buf, size_t size);

const char *wifi_link_hostname(void);

#ifdef __cplusplus
}
#endif
