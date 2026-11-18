#include "printer_state.h"

#include <ctype.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

static const char *TAG = "printer_state";

ESP_EVENT_DEFINE_BASE(PRINTER_STATE_EVENT);

#define BUSY_HOLD_US     (3 * 1000 * 1000)
#define SAMPLE_MIN_US    (1500 * 1000)
#define AUTOREPORT_CMD   "M155 S2"

static SemaphoreHandle_t s_mux;

static struct {
    marlin_temp_t nozzle, bed;
    bool homed;
    bool home_pending;  // a full G28 is in flight, its "ok" sets homed
    bool pos_valid;
    float pos[3];
    int64_t last_busy_us;
} s;

static printer_hist_line_t s_lines[PRINTER_STATE_LINES];
static size_t s_lines_next, s_lines_count;

static printer_temp_sample_t s_temps[PRINTER_STATE_TEMPS];
static size_t s_temps_next, s_temps_count;
static int64_t s_last_sample_us = INT64_MIN / 2;

// ---------------------------------------------------------------- helpers (s_mux held)

static void push_line(bool tx, printer_source_t src, const char *text)
{
    printer_hist_line_t *l = &s_lines[s_lines_next];
    l->tx = tx;
    l->source = src;
    strlcpy(l->text, text, sizeof(l->text));
    s_lines_next = (s_lines_next + 1) % PRINTER_STATE_LINES;
    if (s_lines_count < PRINTER_STATE_LINES) {
        s_lines_count++;
    }
}

static int16_t x10(const marlin_temp_t *t, bool target)
{
    if (!t->valid) {
        return INT16_MIN;
    }
    return (int16_t)lroundf((target ? t->target : t->actual) * 10.0f);
}

static void push_temp_sample(int64_t now)
{
    if (now - s_last_sample_us < SAMPLE_MIN_US) {
        return;
    }
    s_last_sample_us = now;
    printer_temp_sample_t *t = &s_temps[s_temps_next];
    t->time_us = now;
    t->nozzle_x10 = x10(&s.nozzle, false);
    t->nozzle_target_x10 = x10(&s.nozzle, true);
    t->bed_x10 = x10(&s.bed, false);
    t->bed_target_x10 = x10(&s.bed, true);
    s_temps_next = (s_temps_next + 1) % PRINTER_STATE_TEMPS;
    if (s_temps_count < PRINTER_STATE_TEMPS) {
        s_temps_count++;
    }
}

// Code of a G/M word at the start of the line ("N12 G28 X" -> 28 for 'G'), else -1.
// *rest points behind the number.
static int command_code(const char *line, char letter, const char **rest)
{
    while (*line == ' ') {
        line++;
    }
    if (toupper((unsigned char)*line) == 'N') {  // line number prefix
        while (*line && *line != ' ') {
            line++;
        }
        while (*line == ' ') {
            line++;
        }
    }
    if (toupper((unsigned char)*line) != letter || !isdigit((unsigned char)line[1])) {
        return -1;
    }
    int code = 0;
    line++;
    while (isdigit((unsigned char)*line)) {
        code = code * 10 + (*line++ - '0');
    }
    if (*line != '\0' && *line != ' ' && *line != '*' && *line != ';') {
        return -1;  // e.g. "G28.1"
    }
    if (rest) {
        *rest = line;
    }
    return code;
}

static void unhome(void)
{
    s.homed = false;
    s.home_pending = false;
    s.pos_valid = false;
}

// A line the bridge sent. Returns true if the state changed.
static bool on_tx(const printer_tx_event_t *ev)
{
    const char *rest = NULL;
    int g = command_code(ev->text, 'G', &rest);
    int m = command_code(ev->text, 'M', NULL);
    if (g == 28) {
        bool x = false, y = false, z = false;
        for (const char *p = rest; *p && *p != ';' && *p != '*'; p++) {
            char c = (char)toupper((unsigned char)*p);
            x |= c == 'X';
            y |= c == 'Y';
            z |= c == 'Z';
        }
        // "G28" or "G28 X Y Z" homes everything; a single axis doesn't make the printer homed
        s.home_pending = (!x && !y && !z) || (x && y && z);
        return false;
    }
    if (m == 84 || m == 18 || m == 112 || ev->emergency) {
        bool changed = s.homed || s.pos_valid;
        unhome();
        return changed;
    }
    return false;
}

// A line from the printer. Returns true if the state changed.
static bool on_rx(const printer_rx_event_t *ev, int64_t now, bool *send_autoreport)
{
    const marlin_reply_t *r = &ev->reply;
    bool changed = false;

    if (r->nozzle.valid) {
        s.nozzle = r->nozzle;
        changed = true;
    }
    if (r->bed.valid) {
        s.bed = r->bed;
        changed = true;
    }
    if (r->nozzle.valid || r->bed.valid) {
        push_temp_sample(now);
    }

    switch (r->type) {
    case MARLIN_LINE_OK:
        if (s.home_pending) {
            s.home_pending = false;
            s.homed = true;
            changed = true;
        }
        break;
    case MARLIN_LINE_BUSY:
        changed |= now - s.last_busy_us > BUSY_HOLD_US;
        s.last_busy_us = now;
        break;
    case MARLIN_LINE_START:
        unhome();
        s.nozzle.valid = s.bed.valid = false;
        *send_autoreport = true;
        changed = true;
        break;
    case MARLIN_LINE_ERROR:
        if (strstr(ev->text, "kill") || strstr(ev->text, "halted")) {
            unhome();
            changed = true;
        }
        break;
    case MARLIN_LINE_OTHER: {
        float x, y, z;  // M114: "X:10.00 Y:0.00 Z:0.00 E:0.00 Count X:800 ..."
        if (ev->text[0] == 'X' && sscanf(ev->text, "X:%f Y:%f Z:%f", &x, &y, &z) == 3) {
            s.pos[0] = x;
            s.pos[1] = y;
            s.pos[2] = z;
            s.pos_valid = true;
            changed = true;
        }
        break;
    }
    default:
        break;
    }
    return changed;
}

static void on_printer_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    bool changed = false;
    bool autoreport = false;
    int64_t now = esp_timer_get_time();

    xSemaphoreTake(s_mux, portMAX_DELAY);
    if (id == PRINTER_EVENT_RX_LINE) {
        const printer_rx_event_t *ev = data;
        push_line(false, PRINTER_SRC_INTERNAL, ev->text);
        changed = on_rx(ev, now, &autoreport);
    } else if (id == PRINTER_EVENT_TX_LINE) {
        const printer_tx_event_t *ev = data;
        push_line(true, ev->source, ev->text);
        changed = on_tx(ev);
    } else if (id == PRINTER_EVENT_LINK_STATE) {
        changed = true;
    }
    xSemaphoreGive(s_mux);

    if (autoreport) {
        ESP_LOGI(TAG, "printer started, enabling temperature auto report");
        printer_send(AUTOREPORT_CMD, PRINTER_SRC_INTERNAL, 0);
    }
    if (changed) {
        esp_event_post(PRINTER_STATE_EVENT, PRINTER_STATE_EVENT_CHANGED, NULL, 0, 0);
    }
}

// ---------------------------------------------------------------- API

esp_err_t printer_state_start(void)
{
    if (s_mux) {
        return ESP_ERR_INVALID_STATE;
    }
    s_mux = xSemaphoreCreateMutex();
    if (!s_mux) {
        return ESP_ERR_NO_MEM;
    }
    esp_err_t err = esp_event_handler_register(PRINTER_EVENT, ESP_EVENT_ANY_ID, on_printer_event, NULL);
    if (err != ESP_OK) {
        return err;
    }
    // The printer may already be running (bridge reset): its "start" won't come again.
    return printer_send(AUTOREPORT_CMD, PRINTER_SRC_INTERNAL, pdMS_TO_TICKS(100));
}

void printer_state_get(printer_state_t *out)
{
    int64_t now = esp_timer_get_time();
    xSemaphoreTake(s_mux, portMAX_DELAY);
    out->link = printer_link_get_state();
    out->nozzle = s.nozzle;
    out->bed = s.bed;
    out->busy = s.last_busy_us > 0 && now - s.last_busy_us <= BUSY_HOLD_US;
    out->homed = s.homed;
    out->pos_valid = s.pos_valid;
    memcpy(out->pos, s.pos, sizeof(out->pos));
    xSemaphoreGive(s_mux);
    out->queue = printer_link_queue_len();
}

size_t printer_state_copy_lines(printer_hist_line_t *out, size_t max)
{
    xSemaphoreTake(s_mux, portMAX_DELAY);
    size_t n = s_lines_count < max ? s_lines_count : max;
    size_t start = (s_lines_next + PRINTER_STATE_LINES - n) % PRINTER_STATE_LINES;
    for (size_t i = 0; i < n; i++) {
        out[i] = s_lines[(start + i) % PRINTER_STATE_LINES];
    }
    xSemaphoreGive(s_mux);
    return n;
}

size_t printer_state_copy_temps(printer_temp_sample_t *out, size_t max)
{
    xSemaphoreTake(s_mux, portMAX_DELAY);
    size_t n = s_temps_count < max ? s_temps_count : max;
    size_t start = (s_temps_next + PRINTER_STATE_TEMPS - n) % PRINTER_STATE_TEMPS;
    for (size_t i = 0; i < n; i++) {
        out[i] = s_temps[(start + i) % PRINTER_STATE_TEMPS];
    }
    xSemaphoreGive(s_mux);
    return n;
}

const char *printer_source_name(printer_source_t source)
{
    switch (source) {
    case PRINTER_SRC_CONSOLE: return "console";
    case PRINTER_SRC_WEB:     return "web";
    case PRINTER_SRC_TELNET:  return "telnet";
    case PRINTER_SRC_BLE:     return "ble";
    case PRINTER_SRC_JOB:     return "job";
    default:                  return "internal";
    }
}
