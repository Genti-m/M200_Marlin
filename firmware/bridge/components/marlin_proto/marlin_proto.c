#include "marlin_proto.h"

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static bool starts_with(const char *s, const char *prefix)
{
    return strncmp(s, prefix, strlen(prefix)) == 0;
}

static bool starts_with_nocase(const char *s, const char *prefix)
{
    for (; *prefix; s++, prefix++) {
        if (tolower((unsigned char)*s) != tolower((unsigned char)*prefix)) {
            return false;
        }
    }
    return true;
}

// Parse "a /b" at p (p points just after "T:" or "B:"). The target is optional.
static bool parse_temp_at(const char *p, marlin_temp_t *t)
{
    char *end;
    float actual = strtof(p, &end);
    if (end == p) {
        return false;
    }
    t->actual = actual;
    t->target = 0.0f;
    t->valid = true;
    while (*end == ' ') {
        end++;
    }
    if (*end == '/') {
        t->target = strtof(end + 1, NULL);
    }
    return true;
}

bool marlin_parse_temps(const char *line, marlin_temp_t *nozzle, marlin_temp_t *bed)
{
    bool found = false;
    if (nozzle) {
        nozzle->valid = false;
    }
    if (bed) {
        bed->valid = false;
    }
    // Keys must start a word, so "B@:" and "@:" are skipped. The first T: and B: win.
    for (const char *p = line; *p; p++) {
        bool word_start = (p == line) || p[-1] == ' ';
        if (!word_start) {
            continue;
        }
        if (nozzle && !nozzle->valid && p[0] == 'T' && p[1] == ':') {
            found |= parse_temp_at(p + 2, nozzle);
        } else if (bed && !bed->valid && p[0] == 'B' && p[1] == ':') {
            found |= parse_temp_at(p + 2, bed);
        }
    }
    return found;
}

marlin_line_type_t marlin_parse_line(const char *line, marlin_reply_t *out)
{
    marlin_reply_t r = {.type = MARLIN_LINE_OTHER, .resend_line = -1};

    // Work on a copy without trailing CR/LF/spaces.
    char buf[160];
    size_t n = strlen(line);
    if (n >= sizeof(buf)) {
        n = sizeof(buf) - 1;
    }
    memcpy(buf, line, n);
    while (n > 0 && (buf[n - 1] == '\r' || buf[n - 1] == '\n' || buf[n - 1] == ' ')) {
        n--;
    }
    buf[n] = '\0';
    const char *s = buf;

    if (strcmp(s, "ok") == 0 || starts_with(s, "ok ")) {
        r.type = MARLIN_LINE_OK;
        marlin_parse_temps(s, &r.nozzle, &r.bed);  // M105 answer: "ok T:..."
    } else if (starts_with(s, "busy:")) {
        r.type = MARLIN_LINE_BUSY;
    } else if (starts_with_nocase(s, "Error:") || starts_with(s, "!!")) {
        r.type = MARLIN_LINE_ERROR;
    } else if (starts_with_nocase(s, "Resend:") || starts_with(s, "rs ")) {
        r.type = MARLIN_LINE_RESEND;
        const char *p = s + (s[0] == 'r' && s[1] == 's' ? 3 : 7);
        while (*p == ' ' || *p == 'N') {
            p++;
        }
        r.resend_line = strtol(p, NULL, 10);
    } else if (starts_with(s, "echo:")) {
        r.type = MARLIN_LINE_ECHO;
    } else if (strcmp(s, "start") == 0) {
        r.type = MARLIN_LINE_START;
    } else {
        // Auto temperature report (M155): " T:21.5 /0.0 B:20.1 /0.0 @:0 B@:0"
        const char *p = s;
        while (*p == ' ') {
            p++;
        }
        if (p[0] == 'T' && p[1] == ':' && marlin_parse_temps(p, &r.nozzle, &r.bed)) {
            r.type = MARLIN_LINE_TEMP;
        }
    }

    if (out) {
        *out = r;
    }
    return r.type;
}

uint8_t marlin_checksum(const char *s)
{
    uint8_t cs = 0;
    while (*s) {
        cs ^= (uint8_t)*s++;
    }
    return cs;
}

int marlin_format_numbered(char *buf, size_t size, long line_no, const char *cmd)
{
    int len = snprintf(buf, size, "N%ld %s", line_no, cmd);
    if (len < 0 || (size_t)len >= size) {
        return -1;
    }
    uint8_t cs = marlin_checksum(buf);
    int tail = snprintf(buf + len, size - (size_t)len, "*%u", (unsigned)cs);
    if (tail < 0 || (size_t)(len + tail) >= size) {
        return -1;
    }
    return len + tail;
}

const char *marlin_line_type_name(marlin_line_type_t type)
{
    switch (type) {
    case MARLIN_LINE_OK:     return "ok";
    case MARLIN_LINE_BUSY:   return "busy";
    case MARLIN_LINE_ERROR:  return "error";
    case MARLIN_LINE_RESEND: return "resend";
    case MARLIN_LINE_TEMP:   return "temp";
    case MARLIN_LINE_ECHO:   return "echo";
    case MARLIN_LINE_START:  return "start";
    default:                 return "other";
    }
}
