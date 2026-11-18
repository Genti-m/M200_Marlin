// printer_link: owns the UART to Marlin.
//
// Clients call printer_send(): the line goes on a queue, one task sends it and waits for "ok"
// ("busy:" restarts the wait, "Resend:" resends, a timeout marks the link NO_REPLY).
// printer_emergency() skips the queue and writes straight to the UART (M112 / M108 / M410).
// Everything received is published on the default esp_event loop as PRINTER_EVENT_RX_LINE.
#pragma once

#include "esp_err.h"
#include "esp_event.h"
#include "freertos/FreeRTOS.h"
#include "marlin_proto.h"

#ifdef __cplusplus
extern "C" {
#endif

#define PRINTER_LINE_MAX 128

typedef enum {
    PRINTER_SRC_INTERNAL = 0,
    PRINTER_SRC_CONSOLE,
    PRINTER_SRC_WEB,
    PRINTER_SRC_TELNET,
    PRINTER_SRC_BLE,
    PRINTER_SRC_JOB,
} printer_source_t;

typedef enum {
    PRINTER_LINK_UNKNOWN = 0,  // nothing received yet
    PRINTER_LINK_UP,           // the printer is talking
    PRINTER_LINK_NO_REPLY,     // a command got no "ok" within the timeout
} printer_link_state_t;

ESP_EVENT_DECLARE_BASE(PRINTER_EVENT);

enum {
    PRINTER_EVENT_RX_LINE,     // data: printer_rx_event_t
    PRINTER_EVENT_TX_LINE,     // data: printer_tx_event_t
    PRINTER_EVENT_LINK_STATE,  // data: printer_link_state_t
};

typedef struct {
    marlin_reply_t reply;
    char text[PRINTER_LINE_MAX];
} printer_rx_event_t;

typedef struct {
    printer_source_t source;
    bool emergency;
    char text[PRINTER_LINE_MAX];
} printer_tx_event_t;

// Start the UART and the tasks. The default event loop must already exist.
esp_err_t printer_link_start(void);

// Queue one G-code line (without newline). Returns ESP_ERR_TIMEOUT if the queue stays full.
esp_err_t printer_send(const char *line, printer_source_t source, TickType_t wait);

// Clear the queue and write one line straight to the UART. Without EMERGENCY_PARSER in Marlin
// (the case for this printer) it still runs only after the command Marlin is executing.
esp_err_t printer_emergency(const char *line, printer_source_t source);

// True for M112 / M108 / M410, the commands that should use printer_emergency().
bool printer_is_emergency_command(const char *line);

printer_link_state_t printer_link_get_state(void);

const char *printer_link_state_name(printer_link_state_t state);

#ifdef __cplusplus
}
#endif
