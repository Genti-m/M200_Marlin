#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "json_util.h"

void jb_init(jbuf_t *b, size_t initial)
{
    b->buf = malloc(initial);
    b->len = 0;
    b->cap = b->buf ? initial : 0;
    b->oom = !b->buf;
    if (b->buf) {
        b->buf[0] = '\0';
    }
}

void jb_free(jbuf_t *b)
{
    free(b->buf);
    b->buf = NULL;
    b->len = b->cap = 0;
}

static bool reserve(jbuf_t *b, size_t extra)
{
    if (b->oom) {
        return false;
    }
    if (b->len + extra + 1 <= b->cap) {
        return true;
    }
    size_t cap = b->cap ? b->cap : 64;
    while (cap < b->len + extra + 1) {
        cap *= 2;
    }
    char *p = realloc(b->buf, cap);
    if (!p) {
        b->oom = true;
        return false;
    }
    b->buf = p;
    b->cap = cap;
    return true;
}

void jb_raw(jbuf_t *b, const char *s, size_t n)
{
    if (!reserve(b, n)) {
        return;
    }
    memcpy(b->buf + b->len, s, n);
    b->len += n;
    b->buf[b->len] = '\0';
}

void jb_puts(jbuf_t *b, const char *s)
{
    jb_raw(b, s, strlen(s));
}

void jb_printf(jbuf_t *b, const char *fmt, ...)
{
    char tmp[96];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(tmp, sizeof(tmp), fmt, ap);
    va_end(ap);
    if (n < 0) {
        return;
    }
    if ((size_t)n < sizeof(tmp)) {
        jb_raw(b, tmp, (size_t)n);
        return;
    }
    if (!reserve(b, (size_t)n)) {
        return;
    }
    va_start(ap, fmt);
    vsnprintf(b->buf + b->len, (size_t)n + 1, fmt, ap);
    va_end(ap);
    b->len += (size_t)n;
}

void jb_str(jbuf_t *b, const char *s)
{
    jb_raw(b, "\"", 1);
    for (const unsigned char *p = (const unsigned char *)s; *p; p++) {
        unsigned char c = *p;
        if (c == '"' || c == '\\') {
            char esc[2] = {'\\', (char)c};
            jb_raw(b, esc, 2);
        } else if (c < 0x20) {
            char esc[8];
            int n = snprintf(esc, sizeof(esc), "\\u%04x", c);
            jb_raw(b, esc, (size_t)n);
        } else if (c >= 0x80) {
            jb_raw(b, "?", 1);
        } else {
            jb_raw(b, (const char *)&c, 1);
        }
    }
    jb_raw(b, "\"", 1);
}
