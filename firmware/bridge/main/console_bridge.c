#include "console_bridge.h"

#include <stdio.h>
#include <string.h>

#include "driver/usb_serial_jtag.h"
#include "driver/usb_serial_jtag_vfs.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "printer_link.h"

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

static void submit(const char *line)
{
    if (printer_is_emergency_command(line)) {
        printer_emergency(line, PRINTER_SRC_CONSOLE);
    } else if (printer_send(line, PRINTER_SRC_CONSOLE, pdMS_TO_TICKS(100)) != ESP_OK) {
        ESP_LOGW(TAG, "queue full, dropped: %s", line);
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
    printf("\nG-code console: type a command + Enter (M112/M108/M410 clear the queue and go first)\n");
    return ESP_OK;
}
