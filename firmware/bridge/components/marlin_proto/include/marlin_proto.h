// Marlin serial line protocol: classify reply lines, parse temperatures, checksums.
// Pure C (no ESP-IDF includes) so it can be unit tested on the PC.
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    MARLIN_LINE_OTHER = 0,  // anything not recognised (boot text, M115 output, ...)
    MARLIN_LINE_OK,         // "ok", "ok T:..." (M105 answer), "ok N12 P15 B3" (ADVANCED_OK)
    MARLIN_LINE_BUSY,       // "busy: processing" / "busy: paused for user"
    MARLIN_LINE_ERROR,      // "Error:..." or "!!"
    MARLIN_LINE_RESEND,     // "Resend: 12" / "rs 12"
    MARLIN_LINE_TEMP,       // auto report " T:21.5 /0.0 B:20.1 /0.0 @:0 B@:0" (M155)
    MARLIN_LINE_ECHO,       // "echo:..."
    MARLIN_LINE_START,      // "start": the printer has just booted / reset
} marlin_line_type_t;

typedef struct {
    float actual;
    float target;
    bool valid;
} marlin_temp_t;

typedef struct {
    marlin_line_type_t type;
    long resend_line;      // MARLIN_LINE_RESEND: requested line number, else -1
    marlin_temp_t nozzle;  // filled when the line contains "T:" (TEMP or OK)
    marlin_temp_t bed;     // filled when the line contains "B:"
} marlin_reply_t;

// Classify one received line (a trailing CR/LF is ignored). out may be NULL.
marlin_line_type_t marlin_parse_line(const char *line, marlin_reply_t *out);

// Parse "T:a /b" and "B:a /b" anywhere in the line. Returns true if any temp was found.
bool marlin_parse_temps(const char *line, marlin_temp_t *nozzle, marlin_temp_t *bed);

// XOR checksum of all bytes of s, as used by "N<n> <cmd>*<checksum>".
uint8_t marlin_checksum(const char *s);

// Build "N<n> <cmd>*<cs>" into buf. Returns the length, or -1 if buf is too small.
int marlin_format_numbered(char *buf, size_t size, long line_no, const char *cmd);

// Short name of a line type, for logs.
const char *marlin_line_type_name(marlin_line_type_t type);

#ifdef __cplusplus
}
#endif
