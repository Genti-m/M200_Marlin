#include "printer_link.h"

#include <ctype.h>
#include <string.h>

#include "driver/gpio.h"
#include "driver/uart.h"
#include "esp_log.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "sdkconfig.h"

static const char *TAG = "printer_link";

ESP_EVENT_DEFINE_BASE(PRINTER_EVENT);

#define UART_PORT ((uart_port_t)CONFIG_PRINTER_LINK_UART_NUM)
#define RX_BUF_SIZE 2048
#define TX_BUF_SIZE 1024
#define MAX_RESENDS 3

// Notification bits from the RX task to the TX task.
#define NOTIFY_OK     (1u << 0)
#define NOTIFY_BUSY   (1u << 1)
#define NOTIFY_RESEND (1u << 2)
#define NOTIFY_START  (1u << 3)
#define NOTIFY_ALL    0xFFFFFFFFu

typedef struct {
    printer_source_t source;
    char text[PRINTER_LINE_MAX];
} tx_item_t;

static QueueHandle_t s_queue;
static TaskHandle_t s_tx_task;
static volatile printer_link_state_t s_state = PRINTER_LINK_UNKNOWN;

static void set_state(printer_link_state_t state)
{
    if (s_state == state) {
        return;
    }
    s_state = state;
    ESP_LOGI(TAG, "link %s", printer_link_state_name(state));
    esp_event_post(PRINTER_EVENT, PRINTER_EVENT_LINK_STATE, &state, sizeof(state), 0);
}

// One uart_write_bytes() call per line: the driver holds its TX lock for the whole call, so
// printer_emergency() (any task) can't land between a line and its '\n' from the TX task.
// Two calls could interleave into "G1 X10M112\n\n", which Marlin would read as one bad line.
static void uart_write_line(const char *text)
{
    char buf[PRINTER_LINE_MAX + 1];
    size_t n = strnlen(text, PRINTER_LINE_MAX - 1);
    memcpy(buf, text, n);
    buf[n++] = '\n';
    uart_write_bytes(UART_PORT, buf, n);
}

static void post_tx(const char *text, printer_source_t source, bool emergency)
{
    printer_tx_event_t ev = {.source = source, .emergency = emergency};
    strlcpy(ev.text, text, sizeof(ev.text));
    esp_event_post(PRINTER_EVENT, PRINTER_EVENT_TX_LINE, &ev, sizeof(ev), 0);
}

static void handle_rx_line(const char *line)
{
    printer_rx_event_t ev;
    strlcpy(ev.text, line, sizeof(ev.text));
    marlin_parse_line(line, &ev.reply);

    uint32_t bits = 0;
    switch (ev.reply.type) {
    case MARLIN_LINE_OK:     bits = NOTIFY_OK; break;
    case MARLIN_LINE_BUSY:   bits = NOTIFY_BUSY; break;
    case MARLIN_LINE_RESEND: bits = NOTIFY_RESEND; break;
    case MARLIN_LINE_START:  bits = NOTIFY_START; break;
    default: break;
    }
    if (bits) {
        xTaskNotify(s_tx_task, bits, eSetBits);
    }
    if (ev.reply.type == MARLIN_LINE_START) {
        ESP_LOGW(TAG, "printer reset (\"start\" received)");
    }
    set_state(PRINTER_LINK_UP);
    esp_event_post(PRINTER_EVENT, PRINTER_EVENT_RX_LINE, &ev, sizeof(ev), pdMS_TO_TICKS(50));
}

static void rx_task(void *arg)
{
    uint8_t chunk[128];
    char line[PRINTER_LINE_MAX];
    size_t len = 0;

    for (;;) {
        int n = uart_read_bytes(UART_PORT, chunk, sizeof(chunk), pdMS_TO_TICKS(20));
        for (int i = 0; i < n; i++) {
            char c = (char)chunk[i];
            if (c == '\r') {
                continue;
            }
            if (c == '\n') {
                line[len] = '\0';
                if (len > 0) {
                    handle_rx_line(line);
                }
                len = 0;
            } else if (len < sizeof(line) - 1) {
                line[len++] = c;
            }  // longer lines are truncated
        }
    }
}

static void tx_task(void *arg)
{
    tx_item_t item;
    const TickType_t timeout = pdMS_TO_TICKS(CONFIG_PRINTER_LINK_OK_TIMEOUT_MS);

    for (;;) {
        xQueueReceive(s_queue, &item, portMAX_DELAY);

        // Forget any "ok" that arrived while nothing was in flight.
        ulTaskNotifyValueClear(NULL, NOTIFY_ALL);
        xTaskNotifyStateClear(NULL);

        uart_write_line(item.text);
        post_tx(item.text, item.source, false);

        int resends = 0;
        for (;;) {
            uint32_t bits = 0;
            if (xTaskNotifyWait(0, NOTIFY_ALL, &bits, timeout) == pdFALSE) {
                ESP_LOGW(TAG, "no ok for \"%s\" after %d ms", item.text,
                         CONFIG_PRINTER_LINK_OK_TIMEOUT_MS);
                set_state(PRINTER_LINK_NO_REPLY);
                break;
            }
            if (bits & NOTIFY_START) {
                break;  // printer rebooted: the command is gone
            }
            if (bits & NOTIFY_OK) {
                break;
            }
            if ((bits & NOTIFY_RESEND) && resends < MAX_RESENDS) {
                resends++;
                ESP_LOGW(TAG, "resend %d of \"%s\"", resends, item.text);
                uart_write_line(item.text);
            }
            // NOTIFY_BUSY: just wait again with a fresh timeout
        }
    }
}

esp_err_t printer_link_start(void)
{
    if (s_queue) {
        return ESP_ERR_INVALID_STATE;
    }

    const uart_config_t cfg = {
        .baud_rate = CONFIG_PRINTER_LINK_BAUD,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    esp_err_t err = uart_driver_install(UART_PORT, RX_BUF_SIZE, TX_BUF_SIZE, 0, NULL, 0);
    if (err == ESP_OK) {
        err = uart_param_config(UART_PORT, &cfg);
    }
    if (err == ESP_OK) {
        err = uart_set_pin(UART_PORT, CONFIG_PRINTER_LINK_TX_GPIO, CONFIG_PRINTER_LINK_RX_GPIO,
                           UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE);
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "UART setup failed: %s", esp_err_to_name(err));
        return err;
    }
    // Keep RX idle-high when the printer is off, so it doesn't read noise.
    gpio_set_pull_mode(CONFIG_PRINTER_LINK_RX_GPIO, GPIO_PULLUP_ONLY);

    s_queue = xQueueCreate(CONFIG_PRINTER_LINK_QUEUE_LEN, sizeof(tx_item_t));
    if (!s_queue) {
        return ESP_ERR_NO_MEM;
    }
    xTaskCreate(tx_task, "printer_tx", 4096, NULL, 6, &s_tx_task);
    xTaskCreate(rx_task, "printer_rx", 4096, NULL, 7, NULL);

    ESP_LOGI(TAG, "UART%d TX=GPIO%d RX=GPIO%d %d 8N1", CONFIG_PRINTER_LINK_UART_NUM,
             CONFIG_PRINTER_LINK_TX_GPIO, CONFIG_PRINTER_LINK_RX_GPIO, CONFIG_PRINTER_LINK_BAUD);
    return ESP_OK;
}

esp_err_t printer_send(const char *line, printer_source_t source, TickType_t wait)
{
    if (!s_queue || !line) {
        return ESP_ERR_INVALID_STATE;
    }
    tx_item_t item = {.source = source};
    strlcpy(item.text, line, sizeof(item.text));
    return xQueueSend(s_queue, &item, wait) == pdTRUE ? ESP_OK : ESP_ERR_TIMEOUT;
}

esp_err_t printer_emergency(const char *line, printer_source_t source)
{
    if (!s_queue || !line) {
        return ESP_ERR_INVALID_STATE;
    }
    // This Marlin has no EMERGENCY_PARSER (see docs/marlin_caps.md): it runs M112 only after
    // the current command. Drop everything still waiting here so we add no extra delay.
    UBaseType_t dropped = uxQueueMessagesWaiting(s_queue);
    xQueueReset(s_queue);
    uart_write_line(line);  // uart_write_bytes is thread-safe
    ESP_LOGW(TAG, "emergency: %s (%u queued commands dropped)", line, (unsigned)dropped);
    post_tx(line, source, true);
    return ESP_OK;
}

bool printer_is_emergency_command(const char *line)
{
    while (*line == ' ') {
        line++;
    }
    if (toupper((unsigned char)line[0]) != 'M') {
        return false;
    }
    int code = 0;
    const char *p = line + 1;
    if (!isdigit((unsigned char)*p)) {
        return false;
    }
    while (isdigit((unsigned char)*p)) {
        code = code * 10 + (*p++ - '0');
    }
    if (*p != '\0' && *p != ' ') {
        return false;
    }
    return code == 112 || code == 108 || code == 410;
}

printer_link_state_t printer_link_get_state(void)
{
    return s_state;
}

uint32_t printer_link_queue_len(void)
{
    return s_queue ? (uint32_t)uxQueueMessagesWaiting(s_queue) : 0;
}

const char *printer_link_state_name(printer_link_state_t state)
{
    switch (state) {
    case PRINTER_LINK_UP:       return "UP";
    case PRINTER_LINK_NO_REPLY: return "NO_REPLY";
    default:                    return "UNKNOWN";
    }
}
