// printer_link: owns the UART to Marlin.
//
// Clients call printer_send(): the line goes on a queue, one task sends it and waits for "ok"
// ("busy:" restarts the wait, "Resend:" resends, a timeout marks the link NO_REPLY).
// printer_emergency() skips the queue and writes straight to the UART (M112 / M108 / M410).
// Clients don't call these directly for user lines: printer_state_submit() does, after the
// guard.
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

// Every printer_emergency() starts a new epoch. A caller that decides to send a line (checks
// STOP, the guard, a home's abort flag) reads the epoch BEFORE deciding and queues with
// printer_send_at(): if an emergency came in between, the TX task drops the line instead of
// sending it after the STOP. printer_send() uses the epoch current when it is called.
uint32_t printer_link_epoch(void);
esp_err_t printer_send_at(const char *line, printer_source_t source, TickType_t wait,
                          uint32_t epoch);

// Clear the queue and write one line straight to the UART (M112 / M108 / M410: the caller
// decides, see printer_state_submit()). Without EMERGENCY_PARSER in Marlin (the case for this
// printer) it still runs only after the command Marlin is executing.
esp_err_t printer_emergency(const char *line, printer_source_t source);

printer_link_state_t printer_link_get_state(void);

// Lines waiting in the queue (not counting the one in flight).
uint32_t printer_link_queue_len(void);

// Free places in the queue: a batch that must go in whole (a jog) checks this first.
uint32_t printer_link_queue_free(void);

// "unknown" / "up" / "no_reply": logs, the console and the web state's "link" field.
const char *printer_link_state_name(printer_link_state_t state);

#ifdef __cplusplus
}
#endif
