// Minimal JSON helpers for web_server (private).
#pragma once

#include <stdbool.h>
#include <stddef.h>

// ---- output: growable string buffer
typedef struct {
    char *buf;
    size_t len;
    size_t cap;
    bool oom;  // an allocation failed; the content is incomplete
} jbuf_t;

void jb_init(jbuf_t *b, size_t initial);
void jb_free(jbuf_t *b);
void jb_raw(jbuf_t *b, const char *s, size_t n);
void jb_puts(jbuf_t *b, const char *s);
void jb_printf(jbuf_t *b, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
// Quoted, escaped string. Bytes >= 0x80 become '?' so the frame is always valid UTF-8.
void jb_str(jbuf_t *b, const char *s);

// ---- input: just enough for {"t":"gcode","lines":["G28","M114"]} / {"t":"stop"}

// Pointer to the value of "key" in a flat object, or NULL.
const char *jin_find(const char *json, const char *key);

// Read a JSON string at p (must point at '"') into out. Returns the char after the closing
// quote, or NULL on error. \uXXXX outside ASCII becomes '?'. Too long strings are truncated.
const char *jin_string(const char *p, char *out, size_t size);

// Skip whitespace.
const char *jin_ws(const char *p);
