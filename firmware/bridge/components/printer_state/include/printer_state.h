// printer_state: what the bridge knows about the printer, built from the printer_link events.
//
// Tracks temperatures, busy, homed (Marlin doesn't report it: set by the "ok" of an all-axes
// G28, cleared by M84/M18/M112/"start"/kill), the last M114 position, plus a history of
// console lines and of temperatures for the web chart.
// Also sends "M155 S2" at start and after every printer reset, so Marlin reports temps by itself.
// Posts PRINTER_STATE_EVENT_CHANGED on the default loop after every change or temp report.
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"
#include "esp_event.h"
#include "printer_link.h"

#ifdef __cplusplus
extern "C" {
#endif

#define PRINTER_STATE_LINES 100   // console history
#define PRINTER_STATE_TEMPS 300   // temp history, one sample per >= 2 s report: 10 min

ESP_EVENT_DECLARE_BASE(PRINTER_STATE_EVENT);
enum { PRINTER_STATE_EVENT_CHANGED };  // no data

typedef struct {
    printer_link_state_t link;
    marlin_temp_t nozzle;
    marlin_temp_t bed;
    bool busy;       // a "busy:" line within the last 3 s
    bool homed;
    bool pos_valid;
    float pos[3];    // X Y Z from the last M114
    uint32_t queue;  // lines waiting in printer_link
} printer_state_t;

typedef struct {
    bool tx;
    printer_source_t source;  // tx only
    char text[PRINTER_LINE_MAX];
} printer_hist_line_t;

typedef struct {
    int64_t time_us;          // esp_timer_get_time()
    int16_t nozzle_x10, nozzle_target_x10, bed_x10, bed_target_x10;
} printer_temp_sample_t;

esp_err_t printer_state_start(void);  // after printer_link_start()

void printer_state_get(printer_state_t *out);

// Copy the history, oldest first. Return the number of entries written (<= max).
size_t printer_state_copy_lines(printer_hist_line_t *out, size_t max);
size_t printer_state_copy_temps(printer_temp_sample_t *out, size_t max);

const char *printer_source_name(printer_source_t source);

#ifdef __cplusplus
}
#endif
