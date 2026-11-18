#include <string.h>

#include "json_util.h"

const char *jin_ws(const char *p)
{
    while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') {
        p++;
    }
    return p;
}

static int hexval(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

const char *jin_string(const char *p, char *out, size_t size)
{
    if (*p != '"' || size == 0) {
        return NULL;
    }
    p++;
    size_t n = 0;
    while (*p && *p != '"') {
        char c = *p++;
        if (c == '\\') {
            char e = *p++;
            switch (e) {
            case 'n': c = '\n'; break;
            case 't': c = '\t'; break;
            case 'r': c = '\r'; break;
            case 'b': c = '\b'; break;
            case 'f': c = '\f'; break;
            case '"': case '\\': case '/': c = e; break;
            case 'u': {
                int v = 0;
                for (int i = 0; i < 4; i++) {
                    int h = hexval(*p);
                    if (h < 0) {
                        return NULL;
                    }
                    v = v * 16 + h;
                    p++;
                }
                c = (v >= 0x20 && v < 0x80) ? (char)v : '?';
                break;
            }
            default:
                return NULL;
            }
        } else if ((unsigned char)c >= 0x80) {
            c = '?';
        }
        if (n + 1 < size) {
            out[n++] = c;
        }
    }
    out[n] = '\0';
    return *p == '"' ? p + 1 : NULL;
}

const char *jin_find(const char *json, const char *key)
{
    size_t klen = strlen(key);
    const char *p = json;
    while ((p = strchr(p, '"')) != NULL) {
        if (strncmp(p + 1, key, klen) == 0 && p[1 + klen] == '"') {
            const char *v = jin_ws(p + 2 + klen);
            if (*v == ':') {
                return jin_ws(v + 1);
            }
        }
        // skip this string (it may be a value containing the key text)
        p++;
        while (*p && *p != '"') {
            if (*p == '\\' && p[1]) {
                p++;
            }
            p++;
        }
        if (!*p) {
            return NULL;
        }
        p++;
    }
    return NULL;
}
