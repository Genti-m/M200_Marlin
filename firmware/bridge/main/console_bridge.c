#include "console_bridge.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "driver/usb_serial_jtag.h"
#include "driver/usb_serial_jtag_vfs.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_system.h"
#include "printer_link.h"
#include "printer_state.h"
#include "web_server.h"
#include "wifi_link.h"

static const char *TAG = "console";

static void echo(const char *s, size_t len)
{
    usb_serial_jtag_write_bytes(s, len, pdMS_TO_TICKS(20));
}

static void on_printer_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (id == PRINTER_EVENT_RX_LINE) {
        const printer_rx_event_t *ev = data;
        printf("< %s\n", ev->text);
    } else if (id == PRINTER_EVENT_TX_LINE) {
        const printer_tx_event_t *ev = data;
        if (ev->source != PRINTER_SRC_CONSOLE) {
            printf("> %s%s\n", ev->text, ev->emergency ? "   (emergency)" : "");
        }
    }
}

static const char *auth_name(wifi_auth_mode_t m)
{
    switch (m) {
    case WIFI_AUTH_OPEN:            return "open";
    case WIFI_AUTH_WEP:             return "WEP (unsupported)";
    case WIFI_AUTH_WPA_PSK:         return "WPA (unsupported)";
    case WIFI_AUTH_WPA2_PSK:        return "WPA2";
    case WIFI_AUTH_WPA_WPA2_PSK:    return "WPA/WPA2";
    case WIFI_AUTH_WPA3_PSK:        return "WPA3";
    case WIFI_AUTH_WPA2_WPA3_PSK:   return "WPA2/WPA3";
    case WIFI_AUTH_WPA2_ENTERPRISE: return "Enterprise (unsupported)";
    default:                        return "other";
    }
}

static void scan_command(void)
{
    uint16_t n = 20;
    wifi_ap_record_t *aps = calloc(n, sizeof(*aps));
    if (!aps) {
        printf("out of memory\n");
        return;
    }
    printf("scanning...\n");
    esp_err_t err = wifi_link_scan(aps, &n);
    if (err != ESP_OK) {
        printf("scan failed: %s (busy connecting? try again in a few seconds)\n", esp_err_to_name(err));
    } else if (n == 0) {
        printf("no networks found (antenna? metal enclosure?)\n");
    } else {
        printf(" dBm  ch  security                  ssid\n");
        for (int i = 0; i < n; i++) {
            printf("%4d  %2d  %-24s  \"%s\"\n", aps[i].rssi, aps[i].primary,
                   auth_name(aps[i].authmode), (const char *)aps[i].ssid);
        }
    }
    free(aps);
}

// "!wifi <ssid> <password>" (ssid in "quotes" if it has spaces), "!wifi clear", "!net", "!scan",
// "!token [<secret>|clear]", "!reboot", "!homexy", "!homez", "!help"
static void bridge_command(const char *cmd)
{
    if (strcmp(cmd, "wifi clear") == 0) {
        esp_err_t err = wifi_link_clear_credentials();
        if (err == ESP_OK) {
            printf("WiFi credentials cleared, disconnected. Set new ones with !wifi <ssid> <password>\n");
        } else {
            printf("clear failed: %s\n", esp_err_to_name(err));
        }
    } else if (strncmp(cmd, "wifi ", 5) == 0) {
        char ssid[33] = {0};
        const char *p = cmd + 5;
        while (*p == ' ') {
            p++;
        }
        size_t n = 0;
        if (*p == '"') {
            p++;
            while (*p && *p != '"' && n < sizeof(ssid) - 1) {
                ssid[n++] = *p++;
            }
            if (*p == '"') {
                p++;
            }
        } else {
            while (*p && *p != ' ' && n < sizeof(ssid) - 1) {
                ssid[n++] = *p++;
            }
        }
        while (*p == ' ') {
            p++;
        }
        esp_err_t err = wifi_link_set_credentials(ssid, p);  // rest of the line = password
        if (err == ESP_OK) {
            printf("saved, connecting (watch for \"connected, IP\")\n");
        } else {
            printf("usage: !wifi <ssid> <password>   (ssid in \"quotes\" if it has spaces)\n");
        }
    } else if (strcmp(cmd, "net") == 0) {
        char ip[16], ssid[33];
        wifi_link_ip_str(ip, sizeof(ip));
        wifi_link_ssid(ssid, sizeof(ssid));
        if (wifi_link_connected()) {
            printf("WiFi \"%s\" %d dBm, IP %s, http://%s.local\n", ssid, wifi_link_rssi(), ip,
                   wifi_link_hostname());
        } else {
            int reason = wifi_link_last_reason();
            printf("WiFi not connected (ssid \"%s\", last reason %d: %s)\n", ssid, reason,
                   wifi_link_reason_str(reason));
        }
        printf("printer link %s, %u queued\n", printer_link_state_name(printer_link_get_state()),
               (unsigned)printer_link_queue_len());
    } else if (strcmp(cmd, "token") == 0) {
        printf("API token %s\n", web_server_token_is_set()
                                     ? "set: web control and OTA need it (X-Token header)"
                                     : "not set: web control is open, OTA is refused");
    } else if (strcmp(cmd, "token clear") == 0) {
        esp_err_t err = web_server_set_token(NULL);
        if (err == ESP_OK) {
            printf("API token cleared: web control open, OTA refused\n");
        } else {
            printf("clear failed: %s\n", esp_err_to_name(err));
        }
    } else if (strncmp(cmd, "token ", 6) == 0) {
        esp_err_t err = web_server_set_token(cmd + 6);
        if (err == ESP_OK) {
            printf("API token saved (open web pages must log in again)\n");
        } else if (err == ESP_ERR_INVALID_ARG) {
            printf("token must be %d..%d printable characters, no spaces\n", WEB_TOKEN_MIN,
                   WEB_TOKEN_MAX);
        } else {
            printf("save failed: %s\n", esp_err_to_name(err));
        }
    } else if (strcmp(cmd, "scan") == 0) {
        scan_command();
    } else if (strcmp(cmd, "reboot") == 0) {
        printf("rebooting\n");
        vTaskDelay(pdMS_TO_TICKS(100));
        esp_restart();
    } else if (strcmp(cmd, "homexy") == 0) {
        printer_state_home_xy();  // progress and result come as "bridge:" lines
    } else if (strcmp(cmd, "homez") == 0) {
        printer_state_home_z();
    } else {
        printf("bridge commands:\n"
               "  !wifi <ssid> <password>   save WiFi in NVS and connect\n"
               "  !wifi clear               forget WiFi and disconnect\n"
               "  !net                      WiFi / printer status\n"
               "  !scan                     list the WiFi networks the ESP32 can see\n"
               "  !token <secret>           set the web API token (needed for OTA)\n"
               "  !token / !token clear     show whether one is set / remove it\n"
               "  !reboot                   restart the ESP32\n"
               "  !homexy                   home X Y on their endstops, park at 10 (no G28)\n"
               "  !homez                    home Z on the switch (steps + M119), park at 10\n"
               "anything else goes to the printer as G-code\n");
    }
}

static void submit(const char *line)
{
    if (line[0] == '!') {
        bridge_command(line + 1);
        return;
    }
    if (printer_is_emergency_command(line)) {
        printer_emergency(line, PRINTER_SRC_CONSOLE);
    } else if (!printer_state_allow(line, PRINTER_SRC_CONSOLE)) {
        return;  // the reason is printed as an "Error:bridge:" line
    } else if (printer_send(line, PRINTER_SRC_CONSOLE, pdMS_TO_TICKS(100)) != ESP_OK) {
        ESP_LOGW(TAG, "queue full, dropped: %s", line);
        printer_state_dropped(line);
    }
}

static void console_task(void *arg)
{
    char line[PRINTER_LINE_MAX];
    size_t len = 0;
    uint8_t c;

    for (;;) {
        if (usb_serial_jtag_read_bytes(&c, 1, portMAX_DELAY) != 1) {
            continue;
        }
        if (c == '\r' || c == '\n') {
            if (len == 0) {
                continue;  // ignore the LF of a CRLF pair and empty lines
            }
            echo("\r\n", 2);
            line[len] = '\0';
            len = 0;
            submit(line);
        } else if (c == 0x08 || c == 0x7F) {  // backspace / delete
            if (len > 0) {
                len--;
                echo("\b \b", 3);
            }
        } else if (c >= 0x20 && len < sizeof(line) - 1) {
            line[len++] = (char)c;
            echo((const char *)&c, 1);
        }
    }
}

esp_err_t console_bridge_start(void)
{
    usb_serial_jtag_driver_config_t cfg = USB_SERIAL_JTAG_DRIVER_CONFIG_DEFAULT();
    esp_err_t err = usb_serial_jtag_driver_install(&cfg);
    if (err != ESP_OK) {
        return err;
    }
    usb_serial_jtag_vfs_use_driver();  // printf/logs go through the same driver

    err = esp_event_handler_register(PRINTER_EVENT, ESP_EVENT_ANY_ID, on_printer_event, NULL);
    if (err != ESP_OK) {
        return err;
    }
    xTaskCreate(console_task, "console", 4096, NULL, 5, NULL);
    printf("\nG-code console: type a command + Enter (M112/M108/M410 clear the queue and go first)\n"
           "Bridge commands start with '!': !wifi <ssid> <password>, !wifi clear, !net, !scan, !reboot, !help\n");
    return ESP_OK;
}
