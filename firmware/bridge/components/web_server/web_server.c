#include "web_server.h"

#include <ctype.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

#include "esp_app_desc.h"
#include "esp_http_server.h"
#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "api_token.h"
#include "json_util.h"
#include "printer_link.h"
#include "printer_state.h"
#include "wifi_link.h"

static const char *TAG = "web_server";

#define MAX_CLIENTS       7       // httpd max_open_sockets
#define MAX_WS_FRAME      2048
#define MAX_GCODE_BODY    4096
#define MAX_LINES_PER_MSG 32
#define USER_LINE_MAX     96      // docs/protocol.md
#define STATE_PERIOD_US   (5 * 1000 * 1000)

static httpd_handle_t s_server;

static bool origin_ok(httpd_req_t *req);

// ---------------------------------------------------------------- embedded UI

#define ASSET(sym) \
    extern const uint8_t sym##_start[] asm("_binary_" #sym "_start"); \
    extern const uint8_t sym##_end[] asm("_binary_" #sym "_end")
ASSET(index_html_gz);
ASSET(style_css_gz);
ASSET(app_js_gz);

typedef struct {
    const uint8_t *start;
    const uint8_t *end;
    const char *type;
} asset_t;

static const asset_t s_index = {index_html_gz_start, index_html_gz_end, "text/html; charset=utf-8"};
static const asset_t s_css = {style_css_gz_start, style_css_gz_end, "text/css"};
static const asset_t s_js = {app_js_gz_start, app_js_gz_end, "application/javascript"};

static esp_err_t asset_get(httpd_req_t *req)
{
    const asset_t *a = req->user_ctx;
    httpd_resp_set_type(req, a->type);
    httpd_resp_set_hdr(req, "Content-Encoding", "gzip");
    httpd_resp_set_hdr(req, "Cache-Control", "no-cache");
    return httpd_resp_send(req, (const char *)a->start, a->end - a->start);
}

// ---------------------------------------------------------------- JSON builders

static void jb_temp(jbuf_t *b, const marlin_temp_t *t)
{
    if (t->valid) {
        jb_printf(b, "[%.1f,%.0f]", (double)t->actual, (double)t->target);
    } else {
        jb_puts(b, "null");
    }
}

static void jb_state(jbuf_t *b, bool with_type)
{
    printer_state_t s;
    printer_state_get(&s);
    jb_puts(b, with_type ? "{\"t\":\"state\"," : "{");
    jb_printf(b, "\"link\":\"%s\",\"nozzle\":", printer_link_state_name(s.link));
    jb_temp(b, &s.nozzle);
    jb_puts(b, ",\"bed\":");
    jb_temp(b, &s.bed);
#define JB_BOOL(v) ((v) ? "true" : "false")
    jb_printf(b, ",\"busy\":%s,\"homing\":%s,\"stopped\":%s,\"halted\":%s", JB_BOOL(s.busy),
              JB_BOOL(s.homing), JB_BOOL(s.stopped), JB_BOOL(s.halted));
    jb_printf(b, ",\"homed\":[%s,%s,%s],\"mpos\":[", JB_BOOL(s.homed[0]), JB_BOOL(s.homed[1]),
              JB_BOOL(s.homed[2]));
    for (int i = 0; i < 3; i++) {  // machine position (0 = switch) and offset, homed axes only
        if (s.homed[i]) {
            jb_printf(b, "%s%.2f", i ? "," : "", (double)s.mpos[i]);
        } else {
            jb_printf(b, "%snull", i ? "," : "");
        }
    }
    jb_printf(b, "],\"zero\":[%.2f,%.2f,%.2f],\"dir\":[%g,%g,%g],\"max\":[%g,%g,%g]",
              (double)s.zero[0], (double)s.zero[1], (double)s.zero[2], (double)s.dir[0],
              (double)s.dir[1], (double)s.dir[2], (double)s.max_mm[0], (double)s.max_mm[1],
              (double)s.max_mm[2]);
#undef JB_BOOL
    jb_printf(b, ",\"unplugged\":{\"nozzle\":%s,\"bed\":%s},\"pos\":",
              s.nozzle_unplugged ? "true" : "false", s.bed_unplugged ? "true" : "false");
    if (s.pos_valid) {
        jb_printf(b, "[%.2f,%.2f,%.2f]", (double)s.pos[0], (double)s.pos[1], (double)s.pos[2]);
    } else {
        jb_puts(b, "null");
    }
    int rssi = wifi_link_rssi();
    if (rssi) {
        jb_printf(b, ",\"rssi\":%d", rssi);
    } else {
        jb_puts(b, ",\"rssi\":null");
    }
    jb_printf(b, ",\"queue\":%u}", (unsigned)s.queue);
}

// {"t":"settings","v":{"max_x":170,...},"d":{"max_x":[default,min,max,step],...}}
static void jb_settings(jbuf_t *b, bool with_type)
{
    float v[PS_COUNT];
    printer_state_get_settings(v);
    jb_puts(b, with_type ? "{\"t\":\"settings\",\"v\":{" : "{\"v\":{");
    for (int i = 0; i < PS_COUNT; i++) {
        jb_printf(b, "%s\"%s\":%g", i ? "," : "", printer_setting_desc(i)->key, (double)v[i]);
    }
    jb_puts(b, "},\"d\":{");
    for (int i = 0; i < PS_COUNT; i++) {
        const printer_setting_desc_t *d = printer_setting_desc(i);
        jb_printf(b, "%s\"%s\":[%g,%g,%g,%g]", i ? "," : "", d->key, (double)d->def,
                  (double)d->min, (double)d->max, (double)d->step);
    }
    jb_puts(b, "}}");
}

// Settings from a flat JSON object ({"max_x":180,"park":10}): keys left out keep their value.
// True when applied, else false and why not in why.
static bool apply_settings(const char *js, char why[PRINTER_STATE_WHY_MAX])
{
    float v[PS_COUNT];
    printer_state_get_settings(v);
    for (int i = 0; i < PS_COUNT; i++) {
        const char *p = jin_find(js, printer_setting_desc(i)->key);
        if (!p) {
            continue;
        }
        char *end;
        float f = strtof(p, &end);
        if (end == p) {
            strlcpy(why, "Error:bridge: settings: not a number", PRINTER_STATE_WHY_MAX);
            return false;
        }
        v[i] = f;
    }
    return printer_state_set_settings(v, why);
}

static void jb_line(jbuf_t *b, bool tx, printer_source_t src, const char *text)
{
    jb_puts(b, tx ? "{\"t\":\"line\",\"dir\":\"tx\",\"src\":\"" : "{\"t\":\"line\",\"dir\":\"rx\"");
    if (tx) {
        jb_puts(b, printer_source_name(src));
        jb_puts(b, "\"");
    }
    jb_puts(b, ",\"text\":");
    jb_str(b, text);
    jb_puts(b, "}");
}

static void jb_temp_sample(jbuf_t *b, int16_t v)
{
    if (v == INT16_MIN) {
        jb_puts(b, "null");
    } else {
        jb_printf(b, "%.1f", v / 10.0);
    }
}

// The big hello message: history of lines and temps. Built in the httpd task.
static bool build_hello(jbuf_t *b)
{
    printer_hist_line_t *lines = malloc(sizeof(*lines) * PRINTER_STATE_LINES);
    printer_temp_sample_t *temps = malloc(sizeof(*temps) * PRINTER_STATE_TEMPS);
    if (!lines || !temps) {
        free(lines);
        free(temps);
        return false;
    }
    size_t nl = printer_state_copy_lines(lines, PRINTER_STATE_LINES);
    size_t nt = printer_state_copy_temps(temps, PRINTER_STATE_TEMPS);

    jb_puts(b, "{\"t\":\"hello\",\"name\":");
    jb_str(b, wifi_link_hostname());
    jb_puts(b, ",\"fw\":");
    jb_str(b, esp_app_get_description()->version);
    jb_printf(b, ",\"auth\":\"%s\"", web_server_token_is_set() ? "required" : "open");
    jb_puts(b, ",\"history\":[");
    for (size_t i = 0; i < nl; i++) {
        if (i) {
            jb_puts(b, ",");
        }
        jb_line(b, lines[i].tx, lines[i].source, lines[i].text);
    }
    jb_puts(b, "],\"temps\":[");
    int64_t now = esp_timer_get_time();
    bool first = true;
    for (size_t i = 0; i < nt; i++) {
        const printer_temp_sample_t *t = &temps[i];
        if (t->nozzle_x10 == INT16_MIN && t->bed_x10 == INT16_MIN) {
            continue;
        }
        jb_printf(b, "%s[%.1f,", first ? "" : ",", (double)(now - t->time_us) / 1e6);
        first = false;
        jb_temp_sample(b, t->nozzle_x10);
        jb_puts(b, ",");
        jb_temp_sample(b, t->nozzle_target_x10);
        jb_puts(b, ",");
        jb_temp_sample(b, t->bed_x10);
        jb_puts(b, ",");
        jb_temp_sample(b, t->bed_target_x10);
        jb_puts(b, "]");
    }
    jb_puts(b, "]}");
    free(lines);
    free(temps);
    return !b->oom;
}

// ---------------------------------------------------------------- WebSocket send (httpd task)

typedef struct {
    int fd;          // -1: all WebSocket clients
    size_t len;
    char data[];
} ws_msg_t;

static void ws_send_fd(int fd, const char *data, size_t len)
{
    if (httpd_ws_get_fd_info(s_server, fd) != HTTPD_WS_CLIENT_WEBSOCKET) {
        return;
    }
    httpd_ws_frame_t f = {
        .final = true,
        .type = HTTPD_WS_TYPE_TEXT,
        .payload = (uint8_t *)data,
        .len = len,
    };
    if (httpd_ws_send_frame_async(s_server, fd, &f) != ESP_OK) {
        ESP_LOGD(TAG, "send to fd %d failed", fd);
    }
}

static void ws_send_work(void *arg)
{
    ws_msg_t *m = arg;
    if (m->fd >= 0) {
        ws_send_fd(m->fd, m->data, m->len);
    } else {
        size_t n = MAX_CLIENTS;
        int fds[MAX_CLIENTS];
        if (httpd_get_client_list(s_server, &n, fds) == ESP_OK) {
            for (size_t i = 0; i < n; i++) {
                ws_send_fd(fds[i], m->data, m->len);
            }
        }
    }
    free(m);
}

// Hand a built message to the httpd task. Frees the buffer.
static void ws_queue(jbuf_t *b, int fd)
{
    if (s_server && !b->oom && b->len) {
        ws_msg_t *m = malloc(sizeof(*m) + b->len);
        if (m) {
            m->fd = fd;
            m->len = b->len;
            memcpy(m->data, b->buf, b->len);
            if (httpd_queue_work(s_server, ws_send_work, m) != ESP_OK) {
                free(m);
            }
        }
    }
    jb_free(b);
}

static void hello_work(void *arg)
{
    int fd = (int)(intptr_t)arg;
    jbuf_t b;
    jb_init(&b, 8192);
    if (build_hello(&b)) {
        ws_send_fd(fd, b.buf, b.len);
    } else {
        ESP_LOGW(TAG, "no memory for hello");
    }
    jb_free(&b);
    jb_init(&b, 1024);
    jb_settings(&b, true);
    if (!b.oom) {
        ws_send_fd(fd, b.buf, b.len);
    }
    jb_free(&b);
    jb_init(&b, 256);
    jb_state(&b, true);
    if (!b.oom) {
        ws_send_fd(fd, b.buf, b.len);
    }
    jb_free(&b);
}

// ---------------------------------------------------------------- commands from clients

// Trim, then printer_state_submit(): 1 sent / queued, 0 empty / too long / refused, -1 queue
// full: the caller stops the batch (a jog without its G91 would run as an absolute move).
static int submit_line(const char *raw)
{
    char line[USER_LINE_MAX + 1];
    while (*raw == ' ' || *raw == '\t') {
        raw++;
    }
    size_t n = strcspn(raw, "\r\n");
    while (n > 0 && (raw[n - 1] == ' ' || raw[n - 1] == '\t')) {
        n--;
    }
    if (n == 0) {
        return 0;
    }
    if (n > USER_LINE_MAX) {
        ESP_LOGW(TAG, "line too long (%u chars), dropped", (unsigned)n);
        return 0;
    }
    memcpy(line, raw, n);
    line[n] = '\0';
    return printer_state_submit(line, PRINTER_SRC_WEB, pdMS_TO_TICKS(200));
}

// ---------------------------------------------------------------- WebSocket login

// With a token set, a WebSocket needs {"t":"auth","token":"..."} before any command (STOP
// excepted). The session context holds the token generation it logged in with, so a changed
// or cleared token logs every open page out. httpd frees the context when the socket closes.
static bool ws_authed(httpd_req_t *req)
{
    if (!web_server_token_is_set()) {
        return true;
    }
    const uint32_t *gen = req->sess_ctx;
    return gen && *gen == web_token_generation();
}

static void ws_send_auth(int fd, bool ok)
{
    jbuf_t b;
    jb_init(&b, 64);
    jb_printf(&b, "{\"t\":\"auth\",\"ok\":%s,\"required\":%s}", ok ? "true" : "false",
              web_server_token_is_set() ? "true" : "false");
    ws_queue(&b, fd);
}

static void ws_login(httpd_req_t *req, const char *js)
{
    char token[WEB_TOKEN_MAX + 2];  // one more: a too long token must not match when cut
    const char *v = jin_find(js, "token");
    bool ok = !web_server_token_is_set() ||
              (v && jin_string(v, token, sizeof(token)) && web_token_check(token));
    if (ok && web_server_token_is_set()) {
        // Reuse the context once allocated: httpd frees a replaced one by itself.
        uint32_t *gen = req->sess_ctx;
        if (!gen) {
            gen = malloc(sizeof(*gen));
            req->sess_ctx = gen;
            req->free_ctx = NULL;  // free()
        }
        if (gen) {
            *gen = web_token_generation();
        } else {
            ok = false;
        }
    }
    if (!ok) {
        ESP_LOGW(TAG, "WebSocket fd %d: wrong token", httpd_req_to_sockfd(req));
    }
    ws_send_auth(httpd_req_to_sockfd(req), ok);
}

// A batch (a jog: M120 G91 G1 G90 M400 M121 M114) must go into the printer queue whole. Half
// of one (G91 without its G90) would leave Marlin relative, and a dropped line makes the bridge
// forget the homes. So a batch that doesn't fit is refused before any line is queued: the
// click does nothing and the homes stay.
static bool batch_fits(int count)
{
    if ((uint32_t)count <= printer_link_queue_free()) {
        return true;
    }
    ESP_LOGW(TAG, "batch of %d lines refused, %u free in the queue", count,
             (unsigned)printer_link_queue_free());
    printer_state_note("Error:bridge: busy, the last moves are still queued: try again when they finish");
    return false;
}

static void handle_client_json(httpd_req_t *req, const char *js)
{
    char type[16];
    const char *v = jin_find(js, "t");
    if (!v || !jin_string(v, type, sizeof(type))) {
        return;
    }
    if (strcmp(type, "stop") == 0) {  // never needs the token: anyone may stop the machine
        printer_state_soft_stop(PRINTER_SRC_WEB);
        return;
    }
    if (strcmp(type, "auth") == 0) {
        ws_login(req, js);
        return;
    }
    if (!ws_authed(req)) {
        ws_send_auth(httpd_req_to_sockfd(req), false);
        return;
    }
    if (strcmp(type, "restart") == 0) {
        printer_state_restart();
    } else if (strcmp(type, "home_xy") == 0) {
        printer_state_home_xy();  // runs in its own task, can't block the httpd task
    } else if (strcmp(type, "home_z") == 0) {
        printer_state_home_z();
    } else if (strcmp(type, "settings_set") == 0) {
        char why[PRINTER_STATE_WHY_MAX];
        apply_settings(js, why);  // the answer is a "settings" message, or an Error:bridge: line
    } else if (strcmp(type, "gcode") == 0) {
        const char *start = jin_find(js, "lines");
        if (!start || *start != '[') {
            return;
        }
        start = jin_ws(start + 1);
        char line[PRINTER_LINE_MAX];
        // Pass 0 counts the lines, pass 1 sends them: a batch goes in whole or not at all.
        int count = 0;
        for (int pass = 0; pass < 2; pass++) {
            const char *p = start;
            for (int i = 0; i < MAX_LINES_PER_MSG && *p == '"'; i++) {
                p = jin_string(p, line, sizeof(line));
                if (!p) {
                    return;
                }
                if (pass == 0) {
                    count++;
                } else if (submit_line(line) < 0) {
                    return;
                }
                p = jin_ws(p);
                if (*p != ',') {
                    break;
                }
                p = jin_ws(p + 1);
            }
            if (pass == 0 && !batch_fits(count)) {
                return;
            }
        }
    }
}

// Since ESP-IDF 6 the WebSocket handler is no longer called for the handshake GET: these two
// hooks (CONFIG_HTTPD_WS_PRE/POST_HANDSHAKE_CB_SUPPORT, sdkconfig.defaults) take its place.
#if !CONFIG_HTTPD_WS_PRE_HANDSHAKE_CB_SUPPORT || !CONFIG_HTTPD_WS_POST_HANDSHAKE_CB_SUPPORT
#error "enable CONFIG_HTTPD_WS_PRE_HANDSHAKE_CB_SUPPORT and _POST_ (see sdkconfig.defaults)"
#endif

// Before the switch to WebSocket: a foreign page gets a 403, never a socket.
static esp_err_t ws_pre_handshake(httpd_req_t *req)
{
    if (!origin_ok(req)) {
        httpd_resp_send_err(req, HTTPD_403_FORBIDDEN, "foreign Origin");
        return ESP_FAIL;
    }
    return ESP_OK;
}

// After the switch: send the hello (history, settings, state) from the httpd task.
static esp_err_t ws_post_handshake(httpd_req_t *req)
{
    int fd = httpd_req_to_sockfd(req);
    ESP_LOGI(TAG, "WebSocket client fd %d", fd);
    return httpd_queue_work(s_server, hello_work, (void *)(intptr_t)fd);
}

static esp_err_t ws_handler(httpd_req_t *req)
{
    if (req->method == HTTP_GET) {  // not called for the handshake any more (see above)
        return ESP_OK;
    }
    httpd_ws_frame_t f = {.type = HTTPD_WS_TYPE_TEXT};
    esp_err_t err = httpd_ws_recv_frame(req, &f, 0);  // length only
    if (err != ESP_OK) {
        return err;
    }
    if (f.len > MAX_WS_FRAME) {
        ESP_LOGW(TAG, "frame of %u bytes too big", (unsigned)f.len);
        return ESP_FAIL;  // closes the connection
    }
    char *buf = malloc(f.len + 1);
    if (!buf) {
        return ESP_ERR_NO_MEM;
    }
    f.payload = (uint8_t *)buf;
    err = httpd_ws_recv_frame(req, &f, f.len);
    if (err == ESP_OK && f.type == HTTPD_WS_TYPE_TEXT) {
        buf[f.len] = '\0';
        handle_client_json(req, buf);
    }
    free(buf);
    return err;
}

// ---------------------------------------------------------------- REST

static esp_err_t send_json(httpd_req_t *req, const char *json)
{
    httpd_resp_set_type(req, "application/json");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    return httpd_resp_sendstr(req, json);
}

// Browsers send "Origin" with every WebSocket and every cross-site POST. Only our own page
// (Origin host == Host) may drive the machine: a foreign web page open in the same browser
// can't, even with no token set. No Origin (curl, scripts): allowed, the token covers those.
static bool origin_ok(httpd_req_t *req)
{
    char origin[96], host[64];
    size_t olen = httpd_req_get_hdr_value_len(req, "Origin");
    if (olen == 0) {
        return true;
    }
    if (olen >= sizeof(origin) ||
        httpd_req_get_hdr_value_str(req, "Origin", origin, sizeof(origin)) != ESP_OK ||
        httpd_req_get_hdr_value_len(req, "Host") >= sizeof(host) ||
        httpd_req_get_hdr_value_str(req, "Host", host, sizeof(host)) != ESP_OK) {
        return false;
    }
    const char *o = origin;
    if (strncasecmp(o, "http://", 7) == 0) {
        o += 7;
    } else if (strncasecmp(o, "https://", 8) == 0) {
        o += 8;
    } else {
        return false;  // "null" (file:// pages, sandboxed frames) and anything odd
    }
    if (strcasecmp(o, host) == 0) {
        return true;
    }
    ESP_LOGW(TAG, "%s: refused, Origin \"%s\" is not this bridge (\"%s\")", req->uri, origin, host);
    return false;
}

// REST writes: header "X-Token". Without a token set, open, except when always (OTA), which is
// then refused. False = a 401 / 403 answer has been sent.
static bool rest_authed(httpd_req_t *req, bool always)
{
    if (!origin_ok(req)) {
        httpd_resp_send_err(req, HTTPD_403_FORBIDDEN, "foreign Origin");
        return false;
    }
    if (!web_server_token_is_set()) {
        if (always) {
            httpd_resp_send_err(req, HTTPD_403_FORBIDDEN,
                                "set an API token first: !token <secret> on the USB console");
            return false;
        }
        return true;
    }
    char token[WEB_TOKEN_MAX + 1];
    size_t len = httpd_req_get_hdr_value_len(req, "X-Token");
    if (len > 0 && len <= WEB_TOKEN_MAX &&
        httpd_req_get_hdr_value_str(req, "X-Token", token, sizeof(token)) == ESP_OK &&
        web_token_check(token)) {
        return true;
    }
    ESP_LOGW(TAG, "%s: missing or wrong X-Token", req->uri);
    httpd_resp_send_err(req, HTTPD_401_UNAUTHORIZED, "missing or wrong X-Token header");
    return false;
}

static esp_err_t api_state_get(httpd_req_t *req)
{
    jbuf_t b;
    jb_init(&b, 256);
    jb_state(&b, false);
    esp_err_t err = b.oom ? httpd_resp_send_500(req) : send_json(req, b.buf);
    jb_free(&b);
    return err;
}

// Read the whole body (up to max) into a new buffer. NULL + response sent on error.
static char *read_body(httpd_req_t *req, size_t max)
{
    if (req->content_len > max) {
        httpd_resp_send_err(req, HTTPD_413_CONTENT_TOO_LARGE, "body too large");
        return NULL;
    }
    char *buf = malloc(req->content_len + 1);
    if (!buf) {
        httpd_resp_send_500(req);
        return NULL;
    }
    size_t got = 0;
    while (got < req->content_len) {
        int r = httpd_req_recv(req, buf + got, req->content_len - got);
        if (r == HTTPD_SOCK_ERR_TIMEOUT) {
            continue;
        }
        if (r <= 0) {
            free(buf);
            return NULL;
        }
        got += (size_t)r;
    }
    buf[got] = '\0';
    return buf;
}

static esp_err_t api_gcode_post(httpd_req_t *req)
{
    if (!rest_authed(req, false)) {
        return ESP_OK;
    }
    char *body = read_body(req, MAX_GCODE_BODY);
    if (!body) {
        return ESP_FAIL;
    }
    int lines = 0;  // non-blank lines, for the whole-batch check
    for (const char *p = body; *p;) {
        const char *nl = strchr(p, '\n');
        size_t n = nl ? (size_t)(nl - p) : strlen(p);
        if (strspn(p, " \t\r") < n) {
            lines++;
        }
        p += n + (nl ? 1 : 0);
    }
    if (!batch_fits(lines)) {
        free(body);
        httpd_resp_set_status(req, "503 Service Unavailable");
        return send_json(req, "{\"queued\":0,\"error\":\"busy: printer queue full, nothing sent\"}");
    }
    int queued = 0;
    char *save = NULL;
    for (char *l = strtok_r(body, "\n", &save); l; l = strtok_r(NULL, "\n", &save)) {
        int r = submit_line(l);
        if (r < 0) {
            break;
        }
        queued += r;
    }
    free(body);
    char out[32];
    snprintf(out, sizeof(out), "{\"queued\":%d}", queued);
    return send_json(req, out);
}

static esp_err_t api_settings_get(httpd_req_t *req)
{
    jbuf_t b;
    jb_init(&b, 1024);
    jb_settings(&b, false);
    esp_err_t err = b.oom ? httpd_resp_send_500(req) : send_json(req, b.buf);
    jb_free(&b);
    return err;
}

// POST /api/settings {"max_x":180,...}: only the keys given change.
static esp_err_t api_settings_post(httpd_req_t *req)
{
    if (!rest_authed(req, false)) {
        return ESP_OK;
    }
    char *body = read_body(req, 1024);
    if (!body) {
        return ESP_FAIL;
    }
    char why[PRINTER_STATE_WHY_MAX];
    bool ok = apply_settings(body, why);
    free(body);
    if (!ok) {
        jbuf_t b;
        jb_init(&b, 128);
        jb_puts(&b, "{\"ok\":false,\"error\":");
        jb_str(&b, why);
        jb_puts(&b, "}");
        esp_err_t err = b.oom ? httpd_resp_send_500(req) : send_json(req, b.buf);
        jb_free(&b);
        return err;
    }
    return send_json(req, "{\"ok\":true}");
}

// No token needed: anyone may stop the machine (but not a foreign web page).
static esp_err_t api_stop_post(httpd_req_t *req)
{
    if (!origin_ok(req)) {
        return httpd_resp_send_err(req, HTTPD_403_FORBIDDEN, "foreign Origin");
    }
    printer_state_soft_stop(PRINTER_SRC_WEB);
    return send_json(req, "{\"ok\":true}");
}

static esp_err_t api_restart_post(httpd_req_t *req)
{
    if (!rest_authed(req, false)) {
        return ESP_OK;
    }
    printer_state_restart();
    return send_json(req, "{\"ok\":true}");
}

static void restart_task(void *arg)
{
    vTaskDelay(pdMS_TO_TICKS(500));
    esp_restart();
}

// POST /api/ota with the raw app image (build/m200_bridge.bin) as body. Always needs the
// token. The new image boots "pending verify": main.c confirms it once WiFi is back, else the
// bootloader rolls back to this one (CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE).
static esp_err_t api_ota_post(httpd_req_t *req)
{
    if (!rest_authed(req, true)) {
        return ESP_FAIL;  // close the socket instead of reading up to 3 MB of refused image
    }
    const esp_partition_t *part = esp_ota_get_next_update_partition(NULL);
    if (!part) {
        return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "no OTA partition");
    }
    if (req->content_len == 0 || req->content_len > part->size) {
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "bad image size");
    }
    ESP_LOGW(TAG, "OTA: %u bytes -> %s", (unsigned)req->content_len, part->label);

    esp_ota_handle_t ota;
    esp_err_t err = esp_ota_begin(part, OTA_WITH_SEQUENTIAL_WRITES, &ota);
    if (err != ESP_OK) {
        return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, esp_err_to_name(err));
    }
    char *buf = malloc(4096);
    if (!buf) {
        esp_ota_abort(ota);
        return httpd_resp_send_500(req);
    }
    size_t left = req->content_len;
    while (left > 0 && err == ESP_OK) {
        int r = httpd_req_recv(req, buf, left < 4096 ? left : 4096);
        if (r == HTTPD_SOCK_ERR_TIMEOUT) {
            continue;
        }
        if (r <= 0) {
            err = ESP_FAIL;
            break;
        }
        err = esp_ota_write(ota, buf, (size_t)r);
        left -= (size_t)r;
    }
    free(buf);
    if (err != ESP_OK) {
        esp_ota_abort(ota);
        ESP_LOGE(TAG, "OTA failed: %s", esp_err_to_name(err));
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, esp_err_to_name(err));
    }
    err = esp_ota_end(ota);  // checks the image
    if (err == ESP_OK) {
        err = esp_ota_set_boot_partition(part);
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "OTA image rejected: %s", esp_err_to_name(err));
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, esp_err_to_name(err));
    }
    ESP_LOGW(TAG, "OTA done, rebooting into %s", part->label);
    send_json(req, "{\"ok\":true,\"rebooting\":true}");
    xTaskCreate(restart_task, "restart", 2048, NULL, 5, NULL);
    return ESP_OK;
}

// ---------------------------------------------------------------- printer events -> clients

static void on_printer_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    jbuf_t b;
    if (id == PRINTER_EVENT_RX_LINE) {
        const printer_rx_event_t *ev = data;
        jb_init(&b, 192);
        jb_line(&b, false, PRINTER_SRC_INTERNAL, ev->text);
    } else if (id == PRINTER_EVENT_TX_LINE) {
        const printer_tx_event_t *ev = data;
        jb_init(&b, 192);
        jb_line(&b, true, ev->source, ev->text);
    } else {
        return;
    }
    ws_queue(&b, -1);
}

static void broadcast_state(void)
{
    jbuf_t b;
    jb_init(&b, 256);
    jb_state(&b, true);
    ws_queue(&b, -1);
}

static void on_state_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (id == PRINTER_STATE_EVENT_SETTINGS) {
        jbuf_t b;
        jb_init(&b, 1024);
        jb_settings(&b, true);
        ws_queue(&b, -1);
        return;
    }
    broadcast_state();
}

// Also when the printer is off (no temp reports): keeps the dots and RSSI fresh.
static void state_timer_cb(void *arg)
{
    broadcast_state();
}

// ---------------------------------------------------------------- start

esp_err_t web_server_start(void)
{
    if (s_server) {
        return ESP_ERR_INVALID_STATE;
    }
    web_token_load();
    httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
    cfg.max_uri_handlers = 14;
    cfg.max_open_sockets = MAX_CLIENTS;
    cfg.lru_purge_enable = true;
    cfg.stack_size = 8192;
    cfg.send_wait_timeout = 3;

    esp_err_t err = httpd_start(&s_server, &cfg);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "httpd_start: %s", esp_err_to_name(err));
        return err;
    }

    const httpd_uri_t uris[] = {
        {.uri = "/", .method = HTTP_GET, .handler = asset_get, .user_ctx = (void *)&s_index},
        {.uri = "/index.html", .method = HTTP_GET, .handler = asset_get, .user_ctx = (void *)&s_index},
        {.uri = "/style.css", .method = HTTP_GET, .handler = asset_get, .user_ctx = (void *)&s_css},
        {.uri = "/app.js", .method = HTTP_GET, .handler = asset_get, .user_ctx = (void *)&s_js},
        {.uri = "/ws", .method = HTTP_GET, .handler = ws_handler, .is_websocket = true,
         .ws_pre_handshake_cb = ws_pre_handshake, .ws_post_handshake_cb = ws_post_handshake},
        {.uri = "/api/state", .method = HTTP_GET, .handler = api_state_get},
        {.uri = "/api/gcode", .method = HTTP_POST, .handler = api_gcode_post},
        {.uri = "/api/settings", .method = HTTP_GET, .handler = api_settings_get},
        {.uri = "/api/settings", .method = HTTP_POST, .handler = api_settings_post},
        {.uri = "/api/stop", .method = HTTP_POST, .handler = api_stop_post},
        {.uri = "/api/restart", .method = HTTP_POST, .handler = api_restart_post},
        {.uri = "/api/ota", .method = HTTP_POST, .handler = api_ota_post},
    };
    for (size_t i = 0; i < sizeof(uris) / sizeof(uris[0]); i++) {
        ESP_ERROR_CHECK(httpd_register_uri_handler(s_server, &uris[i]));
    }

    ESP_ERROR_CHECK(esp_event_handler_register(PRINTER_EVENT, ESP_EVENT_ANY_ID, on_printer_event, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(PRINTER_STATE_EVENT, ESP_EVENT_ANY_ID,
                                               on_state_event, NULL));
    const esp_timer_create_args_t targs = {.callback = state_timer_cb, .name = "web_state"};
    esp_timer_handle_t timer;
    ESP_ERROR_CHECK(esp_timer_create(&targs, &timer));
    ESP_ERROR_CHECK(esp_timer_start_periodic(timer, STATE_PERIOD_US));

    ESP_LOGI(TAG, "HTTP server on port %d (UI %u+%u+%u bytes gzipped)", cfg.server_port,
             (unsigned)(index_html_gz_end - index_html_gz_start),
             (unsigned)(style_css_gz_end - style_css_gz_start),
             (unsigned)(app_js_gz_end - app_js_gz_start));
    return ESP_OK;
}
