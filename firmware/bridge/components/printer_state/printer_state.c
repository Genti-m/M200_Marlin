#include "printer_state.h"

#include <ctype.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "nvs.h"

static const char *TAG = "printer_state";

ESP_EVENT_DEFINE_BASE(PRINTER_STATE_EVENT);

#define BUSY_HOLD_US     (3 * 1000 * 1000)
#define SAMPLE_MIN_US    (1500 * 1000)
#define AUTOREPORT_CMD   "M155 S2"

// Marlin's HEATER_0_MINTEMP / BED_MINTEMP (Zortrax M200 V01/V03/V04 config). Below this the
// sensor is open (heater unplugged): idle Marlin only reports it, but kill()s as soon as that
// heater's target is above 0 -- including the bed/nozzle preheat that G28 does before it
// probes Z (PREHEAT_BEFORE_PROBING: nozzle 180, bed 30).
#define UNPLUGGED_BELOW_C 5.0f

static SemaphoreHandle_t s_mux;

static const char AXES[3] = {'X', 'Y', 'Z'};

#define SETTINGS_NVS_NS "laser"
static const printer_setting_desc_t SETTINGS[PS_COUNT] = {
    [PS_MAX_X]       = {"max_x", 170, 10, 400, 1},
    [PS_MAX_Y]       = {"max_y", 170, 10, 400, 1},
    [PS_MAX_Z]       = {"max_z", 170, 10, 400, 1},
    [PS_PARK]        = {"park", 10, 1, 50, 1},
    [PS_BACKOFF]     = {"backoff", 1, 0.2f, 5, 0.1f},
    [PS_XY_FAST_F]   = {"xy_fast_f", 3000, 100, 6000, 100},
    [PS_XY_SLOW_F]   = {"xy_slow_f", 300, 30, 1500, 10},
    [PS_Z_DIR]       = {"z_dir", -1, -1, 1, 2},
    [PS_Z_FAST_STEP] = {"z_fast_step", 0.5f, 0.1f, 5, 0.1f},
    [PS_Z_SLOW_STEP] = {"z_slow_step", 0.1f, 0.01f, 1, 0.01f},
    [PS_Z_FAST_F]    = {"z_fast_f", 600, 30, 1500, 10},
    [PS_Z_SLOW_F]    = {"z_slow_f", 120, 10, 600, 10},
    [PS_JOG_F_XY]    = {"jog_f_xy", 3000, 100, 6000, 100},
    [PS_JOG_F_Z]     = {"jog_f_z", 600, 30, 1500, 10},
    [PS_PRESET_F]    = {"preset_f", 3000, 100, 6000, 100},
    [PS_PRESET_EDGE] = {"preset_edge", 10, 0, 40, 1},
};

// One axis as the bridge tracks it from the lines it lets through (Marlin is never homed:
// no G28). l is Marlin's logical position, zero the logical position of the switch found by
// the bridge's home (G92 shifts both), dir +1 when Marlin's numbers grow away from the switch
// (-1 for Z with z_dir +1). The machine position, 0..max, is dir * (l - zero).
typedef struct {
    bool homed;
    float l, zero, dir;
} axis_t;

static float machine(const axis_t *a, float l)
{
    return a->dir * (l - a->zero);
}

static struct {
    float cfg[PS_COUNT];  // settings
    marlin_temp_t nozzle, bed;
    axis_t ax[3];
    bool relative;       // G91
    uint8_t home_mask;   // axes the next internal G92 marks homed (end of a home task)
    bool homing;         // a home task is running: user G-code refused
    bool home_abort;     // set by STOP / kill / reset / emergency: the running home gives up.
                         // Only a new home clears it, so RESTART or a reboot can't revive one.
    bool sw[3];          // last M119: x_min, y_min, z_probe pressed
    bool pos_valid;
    float pos[3];
    int64_t last_busy_us;
    bool stopped;  // soft STOP pressed, until printer_state_restart()
    bool halted;   // Marlin called kill(): no input until the printer is reset ("start")
} s;

static SemaphoreHandle_t s_sw_sem;  // given on every M119 "z_probe:" line (its last one)

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
// *rest points behind the number. Marlin needs no spaces ("N12G28X", "M104S200"), so the
// number may be followed directly by the next word.
static int command_code(const char *line, char letter, const char **rest)
{
    while (*line == ' ') {
        line++;
    }
    if (toupper((unsigned char)*line) == 'N' && isdigit((unsigned char)line[1])) {  // line number
        line++;
        while (isdigit((unsigned char)*line)) {
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
    if (*line == '.') {
        return -1;  // e.g. "G28.1"
    }
    if (rest) {
        *rest = line;
    }
    return code;
}

static void unhome(void)
{
    for (int i = 0; i < 3; i++) {
        s.ax[i].homed = false;
    }
    s.home_mask = 0;
    s.pos_valid = false;
}

// Value of one word in what follows the command code (" S200", 'S' -> 200). A word starts
// after anything that isn't a letter, so "X10Y20" is two words like Marlin reads it.
static bool num_param(const char *rest, char letter, float *out)
{
    char prev = ' ';
    for (const char *p = rest; *p && *p != ';' && *p != '*'; prev = *p++) {
        if (toupper((unsigned char)*p) == letter && !isalpha((unsigned char)prev)) {
            char *end;
            float v = strtof(p + 1, &end);
            if (end != p + 1) {
                *out = v;
                return true;
            }
        }
    }
    return false;
}

// Target of a heater command: S, or R for M109/M190 ("wait, also when cooling").
static bool target_param(const char *rest, float *out)
{
    return num_param(rest, 'S', out) || num_param(rest, 'R', out);
}

static bool unplugged(const marlin_temp_t *t)
{
    return t->valid && t->actual < UNPLUGGED_BELOW_C;
}

// kill() turns every heater off; Marlin can't report that any more, so show it ourselves.
static void halt(void)
{
    unhome();
    s.home_abort = true;
    s.halted = true;
    s.nozzle.target = 0;
    s.bed.target = 0;
}

// A line the bridge sent. Returns true if the state changed.
static bool on_tx(const printer_tx_event_t *ev)
{
    const char *rest = NULL;
    int g = command_code(ev->text, 'G', &rest);
    int m = command_code(ev->text, 'M', g < 0 ? &rest : NULL);
    float target;
    if ((m == 104 || m == 109 || m == 140 || m == 190) && target_param(rest, &target)) {
        // Show a new target at once (Off -> 0), the next temp report confirms it.
        marlin_temp_t *t = (m == 140 || m == 190) ? &s.bed : &s.nozzle;
        t->target = target;
        return t->valid;
    }
    if (ev->emergency) {
        s.home_abort = true;  // its queued lines are dropped
    }
    if (m == 84 || m == 18 || m == 112 || ev->emergency) {
        bool changed = s.pos_valid || s.ax[0].homed || s.ax[1].homed || s.ax[2].homed;
        unhome();
        return changed;
    }
    return false;
}

// Follow a line on its way to the printer (accepted user line or a home task line): G90/G91,
// G92 and moves update the homed axes. s_mux held.
static void track(const char *line)
{
    const char *rest = NULL;
    int g = command_code(line, 'G', &rest);
    if (g == 90 || g == 91) {
        s.relative = g == 91;
        return;
    }
    if (g != 92 && (g < 0 || g > 3)) {
        return;
    }
    for (int i = 0; i < 3; i++) {
        axis_t *a = &s.ax[i];
        float v;
        if (!num_param(rest, AXES[i], &v)) {
            continue;
        }
        if (g == 92 && (s.home_mask & (1u << i))) {  // end of a home: the switch is 0
            s.home_mask &= ~(1u << i);
            a->homed = true;
            a->dir = i == 2 ? -s.cfg[PS_Z_DIR] : 1;
            a->l = a->zero = v;
        } else if (!a->homed) {
            continue;
        } else if (g == 92) {  // same place, new number
            a->zero += v - a->l;
            a->l = v;
        } else {
            a->l = s.relative ? a->l + v : v;
        }
    }
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
    // M119, read by the home tasks: "x_min: open", "y_min: TRIGGERED", "z_probe: open" (last)
    static const char *const sw_names[3] = {"x_min:", "y_min:", "z_probe:"};
    for (int i = 0; i < 3; i++) {
        if (strncmp(ev->text, sw_names[i], strlen(sw_names[i])) == 0) {
            s.sw[i] = strstr(ev->text, "TRIGGERED") != NULL;
            if (i == 2) {
                xSemaphoreGive(s_sw_sem);
            }
        }
    }

    switch (r->type) {
    case MARLIN_LINE_BUSY:
        changed |= now - s.last_busy_us > BUSY_HOLD_US;
        s.last_busy_us = now;
        break;
    case MARLIN_LINE_START:
        unhome();
        s.home_abort = true;  // a home's moves must not go on into a fresh Marlin
        s.relative = false;  // Marlin boots in G90
        s.halted = false;
        s.stopped = false;  // fresh boot: nothing left to stop
        s.nozzle.valid = s.bed.valid = false;
        *send_autoreport = true;
        changed = true;
        break;
    case MARLIN_LINE_ERROR:
        // "Error:Printer halted. kill() called!" (after M112, MINTEMP, MAXTEMP, thermal runaway)
        if (strstr(ev->text, "kill") || strstr(ev->text, "halted")) {
            halt();
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

// ---------------------------------------------------------------- settings

const printer_setting_desc_t *printer_setting_desc(printer_setting_t i)
{
    return i < PS_COUNT ? &SETTINGS[i] : NULL;
}

// Each setting is its own NVS key (float bits as u32): adding one later keeps the others.
static void settings_load(float *v)
{
    nvs_handle_t h;
    bool open = nvs_open(SETTINGS_NVS_NS, NVS_READONLY, &h) == ESP_OK;
    for (int i = 0; i < PS_COUNT; i++) {
        uint32_t bits;
        float f;
        v[i] = SETTINGS[i].def;
        if (open && nvs_get_u32(h, SETTINGS[i].key, &bits) == ESP_OK) {
            memcpy(&f, &bits, sizeof(f));
            if (f >= SETTINGS[i].min && f <= SETTINGS[i].max) {
                v[i] = f;
            }
        }
    }
    if (open) {
        nvs_close(h);
    }
}

static esp_err_t settings_save(const float *v)
{
    nvs_handle_t h;
    esp_err_t err = nvs_open(SETTINGS_NVS_NS, NVS_READWRITE, &h);
    if (err != ESP_OK) {
        return err;
    }
    for (int i = 0; i < PS_COUNT && err == ESP_OK; i++) {
        uint32_t bits;
        memcpy(&bits, &v[i], sizeof(bits));
        err = nvs_set_u32(h, SETTINGS[i].key, bits);
    }
    if (err == ESP_OK) {
        err = nvs_commit(h);
    }
    nvs_close(h);
    return err;
}

// Why these values can't be used together, or NULL.
static const char *settings_check(const float *v)
{
    static char msg[96];  // only the web / REST tasks call this, one message at a time
    for (int i = 0; i < PS_COUNT; i++) {
        const printer_setting_desc_t *d = &SETTINGS[i];
        if (!(v[i] >= d->min && v[i] <= d->max)) {
            snprintf(msg, sizeof(msg), "Error:bridge: setting %s must be %g..%g", d->key,
                     (double)d->min, (double)d->max);
            return msg;
        }
    }
    if (v[PS_Z_DIR] != -1 && v[PS_Z_DIR] != 1) {
        return "Error:bridge: setting z_dir must be -1 or 1";
    }
    for (int i = 0; i < 3; i++) {
        if (v[PS_PARK] >= v[PS_MAX_X + i]) {
            return "Error:bridge: setting park must be below every axis max";
        }
    }
    if (v[PS_BACKOFF] >= v[PS_PARK]) {
        return "Error:bridge: setting backoff must be below park";
    }
    if (v[PS_XY_SLOW_F] > v[PS_XY_FAST_F] || v[PS_Z_SLOW_F] > v[PS_Z_FAST_F] ||
        v[PS_Z_SLOW_STEP] > v[PS_Z_FAST_STEP]) {
        return "Error:bridge: the slow pass can't be faster or coarser than the seek";
    }
    return NULL;
}

void printer_state_get_settings(float out[PS_COUNT])
{
    xSemaphoreTake(s_mux, portMAX_DELAY);
    memcpy(out, s.cfg, sizeof(s.cfg));
    xSemaphoreGive(s_mux);
}

static void post_note(const char *text);

const char *printer_state_set_settings(const float in[PS_COUNT])
{
    const char *why = settings_check(in);
    xSemaphoreTake(s_mux, portMAX_DELAY);
    if (!why && s.homing) {
        why = "Error:bridge: settings can't change while homing";
    }
    bool same = !why && memcmp(in, s.cfg, sizeof(s.cfg)) == 0;
    if (!why && !same) {
        memcpy(s.cfg, in, sizeof(s.cfg));
    }
    xSemaphoreGive(s_mux);
    if (why) {
        post_note(why);
        return why;
    }
    if (!same) {
        esp_err_t err = settings_save(in);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "settings save: %s", esp_err_to_name(err));
            post_note("Error:bridge: settings applied, but not saved (NVS): lost on reboot");
        } else {
            post_note("echo:bridge: settings saved");
        }
    }
    // Also when unchanged: the client that sent them gets them back and closes its sheet.
    esp_event_post(PRINTER_STATE_EVENT, PRINTER_STATE_EVENT_SETTINGS, NULL, 0, 0);
    esp_event_post(PRINTER_STATE_EVENT, PRINTER_STATE_EVENT_CHANGED, NULL, 0, 0);
    return NULL;
}

// ---------------------------------------------------------------- API

esp_err_t printer_state_start(void)
{
    if (s_mux) {
        return ESP_ERR_INVALID_STATE;
    }
    s_mux = xSemaphoreCreateMutex();
    s_sw_sem = xSemaphoreCreateBinary();
    if (!s_mux || !s_sw_sem) {
        return ESP_ERR_NO_MEM;
    }
    settings_load(s.cfg);
    if (settings_check(s.cfg)) {  // each one in range, but not together: back to defaults
        for (int i = 0; i < PS_COUNT; i++) {
            s.cfg[i] = SETTINGS[i].def;
        }
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
    for (int i = 0; i < 3; i++) {
        out->homed[i] = s.ax[i].homed;
        out->mpos[i] = machine(&s.ax[i], s.ax[i].l);
        out->zero[i] = s.ax[i].zero;
        out->dir[i] = s.ax[i].homed ? s.ax[i].dir : 1;
        out->max_mm[i] = s.cfg[PS_MAX_X + i];
    }
    out->homing = s.homing;
    out->stopped = s.stopped;
    out->halted = s.halted;
    out->nozzle_unplugged = unplugged(&s.nozzle);
    out->bed_unplugged = unplugged(&s.bed);
    out->pos_valid = s.pos_valid;
    memcpy(out->pos, s.pos, sizeof(out->pos));
    xSemaphoreGive(s_mux);
    out->queue = printer_link_queue_len();
}

// A note from the bridge itself, shown like a printer line (console, web, history).
// Posted as an RX line so every client sees it; the text must not contain "kill"/"halted".
static void post_note(const char *text)
{
    printer_rx_event_t ev;
    strlcpy(ev.text, text, sizeof(ev.text));
    marlin_parse_line(ev.text, &ev.reply);
    esp_event_post(PRINTER_EVENT, PRINTER_EVENT_RX_LINE, &ev, sizeof(ev), pdMS_TO_TICKS(50));
}

// Why a line must not go to the printer now, or NULL. s_mux held.
static const char *refusal(const char *line, printer_source_t source)
{
    if (s.stopped && source != PRINTER_SRC_CONSOLE) {
        return "Error:bridge: stopped, press RESTART first";
    }
    if (s.homing) {
        return "Error:bridge: homing running, wait for it (STOP aborts it)";
    }
    const char *rest = NULL;
    int g = command_code(line, 'G', &rest);
    int m = command_code(line, 'M', g < 0 ? &rest : NULL);
    float target;

    // Laser build: heaters and sensors are removed (road_Change.md ch. 1). Marlin kill()s
    // (MINTEMP / heating failed) as soon as a heater target is above 0, so none ever goes out.
    // Target 0 (off) is harmless and still passes: soft STOP sends it.
    if ((m == 104 || m == 109 || m == 140 || m == 190) && target_param(rest, &target) &&
        target > 0) {
        return "Error:bridge: laser build, no heaters: heating refused (S0 / off works)";
    }
    if (m == 303) {
        return "Error:bridge: laser build, no heaters: PID autotune refused";
    }
    // Probing preheats nozzle 180 / bed 30 first (PREHEAT_BEFORE_PROBING): MINTEMP kill.
    // G28 homes Z with the probe, so it does the same after moving to the bed center, and
    // the Zortrax G28 homes Z even for "G28 R0 X Y". So no G28 at all: the web Home seeks
    // the X/Y endstops with plain moves (road_Change.md ch. 3).
    if (g == 29 || g == 30 || m == 48) {
        return "Error:bridge: laser build, probing refused (its preheat stops Marlin)";
    }
    if (g == 28) {
        return "Error:bridge: laser build, G28 refused (its Z preheat stops Marlin). Use the Home button";
    }
    // Homed axes stay within 0..max (settings) of the switch the bridge's home found (Marlin has
    // no soft limits: it is never homed). Arcs: only the end point is checked.
    if (g >= 0 && g <= 3) {
        for (int i = 0; i < 3; i++) {
            const axis_t *a = &s.ax[i];
            float v;
            if (!a->homed || !num_param(rest, AXES[i], &v)) {
                continue;
            }
            float m_to = machine(a, s.relative ? a->l + v : v);
            float max = s.cfg[PS_MAX_X + i];
            if (!(m_to >= -0.001f && m_to <= max + 0.001f)) {  // also refuses "Xnan"
                static char msg[96];  // s_mux held
                snprintf(msg, sizeof(msg), "Error:bridge: %c would go to %.2f mm from home, limit 0..%g",
                         AXES[i], (double)m_to, (double)max);
                return msg;
            }
        }
    }
    return NULL;
}

bool printer_state_allow(const char *line, printer_source_t source)
{
    xSemaphoreTake(s_mux, portMAX_DELAY);
    const char *why = refusal(line, source);
    if (!why) {
        track(line);
    }
    xSemaphoreGive(s_mux);
    if (why) {
        ESP_LOGW(TAG, "refused \"%s\": %s", line, why);
        post_note(why);
    }
    return why == NULL;
}

void printer_state_dropped(const char *line)
{
    xSemaphoreTake(s_mux, portMAX_DELAY);
    bool had = s.ax[0].homed || s.ax[1].homed || s.ax[2].homed;
    unhome();
    xSemaphoreGive(s_mux);
    char msg[96];
    snprintf(msg, sizeof(msg), "Error:bridge: queue full, \"%.24s\" dropped%s", line,
             had ? ": home lost, home again" : "");
    post_note(msg);
    esp_event_post(PRINTER_STATE_EVENT, PRINTER_STATE_EVENT_CHANGED, NULL, 0, 0);
}

// ---------------------------------------------------------------- homing without G28

// G28 homes Z with the probe and preheats for it (MINTEMP kill), so the bridge homes each
// axis with plain moves and reads the switches with M119 (road_Change.md ch. 3):
//  - X/Y: Marlin stops a plain move on x_min / y_min while M120 is on, so one long move in,
//    1 mm back, a slow move in, and M119 checks the switch really is pressed.
//  - Z: Marlin reads its switch (the probe line) only while probing, and G38 isn't built in:
//    the bridge steps Z and reads M119 after each step, 1 mm back, then 0.1 mm steps.
// Then G92 sets the switch as 0 (track() marks the axis homed) and the axis parks at
// the park setting, off the switch. Z moves are absolute after G92 Z0, so a stop halfway
// doesn't leave Marlin in relative mode.
#define SW_REPLY_MS 20000

// The settings for the running home: copied when it starts (they can't change while homing).
static float hc[PS_COUNT];

// Seek a bit more than the whole travel, so "not reached" means the switch is missing.
static float home_seek_mm(int axis)
{
    return hc[PS_MAX_X + axis] + 30;
}

static const char *const HOME_FAILED = "Error:bridge: homing failed (no reply or STOP)";

static bool home_aborted(void)
{
    xSemaphoreTake(s_mux, portMAX_DELAY);
    bool a = s.home_abort || s.stopped || s.halted;
    xSemaphoreGive(s_mux);
    return a;
}

static bool home_send(const char *line)
{
    if (home_aborted() || printer_send(line, PRINTER_SRC_INTERNAL, pdMS_TO_TICKS(1000)) != ESP_OK) {
        return false;
    }
    xSemaphoreTake(s_mux, portMAX_DELAY);
    track(line);
    xSemaphoreGive(s_mux);
    return true;
}

// fmt takes the value then the feedrate, e.g. "G1 Z%.3f F%d".
static bool home_sendf(const char *fmt, float v, int feed)
{
    char line[48];
    snprintf(line, sizeof(line), fmt, (double)v, feed);
    return home_send(line);
}

// Wait for the moves, read the switches: 1 pressed, 0 open, -1 abort/error.
static int home_switch(int axis)
{
    xSemaphoreTake(s_sw_sem, 0);  // drop a stale reply
    if (!home_send("M400") || !home_send("M119")) {
        return -1;
    }
    // STOP clears the queue, so the reply may never come: look at it every 500 ms.
    bool got = false;
    for (int ms = 0; !got && ms < SW_REPLY_MS && !home_aborted(); ms += 500) {
        got = xSemaphoreTake(s_sw_sem, pdMS_TO_TICKS(500)) == pdTRUE;
    }
    if (!got || home_aborted()) {
        return -1;
    }
    xSemaphoreTake(s_mux, portMAX_DELAY);
    int pressed = s.sw[axis];
    xSemaphoreGive(s_mux);
    return pressed;
}

// One X/Y axis, relative moves (G91 already sent), endstop checking on.
static const char *home_xy_axis(int i)
{
    char fmt[24];
    snprintf(fmt, sizeof(fmt), "G1 %c%%.2f F%%d", AXES[i]);
    int fast = (int)hc[PS_XY_FAST_F], slow = (int)hc[PS_XY_SLOW_F];
    if (!home_sendf(fmt, -home_seek_mm(i), fast)) {
        return HOME_FAILED;
    }
    int r = home_switch(i);
    if (r <= 0) {
        return r < 0 ? HOME_FAILED
               : i == 0 ? "Error:bridge: X endstop not reached (max X + 30 mm)"
                        : "Error:bridge: Y endstop not reached (max Y + 30 mm)";
    }
    if (!home_sendf(fmt, hc[PS_BACKOFF], fast) || !home_sendf(fmt, -2 * hc[PS_BACKOFF], slow)) {
        return HOME_FAILED;
    }
    r = home_switch(i);
    if (r <= 0) {
        return r < 0 ? HOME_FAILED
               : i == 0 ? "Error:bridge: X endstop not found again on the slow pass"
                        : "Error:bridge: Y endstop not found again on the slow pass";
    }
    return NULL;
}

static const char *home_xy_run(void)
{
    if (!home_send("M120") || !home_send("G91")) {
        return HOME_FAILED;
    }
    const char *why = home_xy_axis(0);
    if (!why) {
        why = home_xy_axis(1);
    }
    if (!home_send("G90") || !home_send("M121")) {  // also after a failure
        return why ? why : HOME_FAILED;
    }
    if (why) {
        return why;
    }
    xSemaphoreTake(s_mux, portMAX_DELAY);
    s.home_mask |= 1u << 0 | 1u << 1;
    xSemaphoreGive(s_mux);
    char park[40];
    snprintf(park, sizeof(park), "G1 X%.1f Y%.1f F%d", (double)hc[PS_PARK], (double)hc[PS_PARK],
             (int)hc[PS_XY_FAST_F]);
    if (!home_send("G92 X0 Y0") || !home_send(park) || !home_send("M114")) {
        return HOME_FAILED;
    }
    return NULL;
}

// Step Z toward the switch until pressed: 1 pressed, 0 not within max_mm, -1 abort/error.
static int home_z_seek(float *z, float step, int feed, float max_mm)
{
    for (float done = 0; done < max_mm; done += step) {
        *z += hc[PS_Z_DIR] * step;
        if (!home_sendf("G1 Z%.3f F%d", *z, feed)) {
            return -1;
        }
        int r = home_switch(2);
        if (r != 0) {
            return r;
        }
    }
    return 0;
}

// Back off until the switch is open again: 1 open, 0 still pressed, -1 abort/error.
static int home_z_back_off(float *z)
{
    *z -= hc[PS_Z_DIR] * hc[PS_BACKOFF];
    if (!home_sendf("G1 Z%.3f F%d", *z, (int)hc[PS_Z_FAST_F])) {
        return -1;
    }
    int r = home_switch(2);
    return r < 0 ? -1 : !r;
}

static const char *home_z_run(void)
{
    float z = 0;
    if (!home_send("G90") || !home_send("G92 Z0")) {
        return HOME_FAILED;
    }
    int r = home_switch(2);
    if (r == 1) {
        r = home_z_back_off(&z);  // started on the switch
        if (r == 0) {
            return "Error:bridge: Z homing: switch stays pressed";
        }
    }
    if (r < 0) {
        return HOME_FAILED;
    }
    r = home_z_seek(&z, hc[PS_Z_FAST_STEP], (int)hc[PS_Z_FAST_F], home_seek_mm(2));
    if (r <= 0) {
        return r < 0 ? HOME_FAILED : "Error:bridge: Z switch not reached (max Z + 30 mm)";
    }
    r = home_z_back_off(&z);
    if (r <= 0) {
        return r < 0 ? HOME_FAILED : "Error:bridge: Z switch stays pressed after backing off";
    }
    r = home_z_seek(&z, hc[PS_Z_SLOW_STEP], (int)hc[PS_Z_SLOW_F],
                    hc[PS_BACKOFF] + 2 * hc[PS_Z_FAST_STEP]);
    if (r <= 0) {
        return r < 0 ? HOME_FAILED : "Error:bridge: Z switch not found again on the slow pass";
    }
    xSemaphoreTake(s_mux, portMAX_DELAY);
    s.home_mask |= 1u << 2;
    xSemaphoreGive(s_mux);
    // Park away from the switch: Marlin Z +park for z_dir -1, -park for z_dir +1 (track()
    // counts the machine position away from the switch either way).
    if (!home_send("G92 Z0") ||
        !home_sendf("G1 Z%.1f F%d", -hc[PS_Z_DIR] * hc[PS_PARK], (int)hc[PS_Z_FAST_F]) ||
        !home_send("M114")) {
        return HOME_FAILED;
    }
    return NULL;
}

typedef struct {
    const char *(*run)(void);
    const char *done;
} home_job_t;

static const home_job_t HOME_XY_JOB = {home_xy_run, "echo:bridge: X Y homed, parked off the switches"};
static const home_job_t HOME_Z_JOB = {home_z_run, "echo:bridge: Z homed, parked off the switch"};

static void home_task(void *arg)
{
    const home_job_t *job = arg;
    const char *why = job->run();
    xSemaphoreTake(s_mux, portMAX_DELAY);
    s.homing = false;
    s.home_mask = 0;
    xSemaphoreGive(s_mux);
    if (why) {
        ESP_LOGW(TAG, "%s", why);
    }
    post_note(why ? why : job->done);
    esp_event_post(PRINTER_STATE_EVENT, PRINTER_STATE_EVENT_CHANGED, NULL, 0, 0);
    vTaskDelete(NULL);
}

// The axes being homed lose their homed state at once: their position is unknown until done.
static esp_err_t home_start(const home_job_t *job, uint8_t axes, const char *note)
{
    xSemaphoreTake(s_mux, portMAX_DELAY);
    bool busy = s.homing || s.stopped || s.halted || printer_link_get_state() != PRINTER_LINK_UP;
    if (!busy) {
        s.homing = true;
        s.home_abort = false;
        memcpy(hc, s.cfg, sizeof(hc));
        for (int i = 0; i < 3; i++) {
            if (axes & (1u << i)) {
                s.ax[i].homed = false;
            }
        }
    }
    xSemaphoreGive(s_mux);
    if (busy) {
        post_note("Error:bridge: homing not possible now (running, stopped, halted or no printer)");
        return ESP_ERR_INVALID_STATE;
    }
    if (xTaskCreate(home_task, "home", 4096, (void *)job, 4, NULL) != pdPASS) {
        xSemaphoreTake(s_mux, portMAX_DELAY);
        s.homing = false;
        xSemaphoreGive(s_mux);
        return ESP_ERR_NO_MEM;
    }
    post_note(note);
    esp_event_post(PRINTER_STATE_EVENT, PRINTER_STATE_EVENT_CHANGED, NULL, 0, 0);
    return ESP_OK;
}

esp_err_t printer_state_home_xy(void)
{
    return home_start(&HOME_XY_JOB, 1u << 0 | 1u << 1, "echo:bridge: homing X Y on their endstops...");
}

esp_err_t printer_state_home_z(void)
{
    return home_start(&HOME_Z_JOB, 1u << 2, "echo:bridge: homing Z on the switch (steps + M119)...");
}

// Soft stop: Marlin keeps running, so the page stays live and no power cycle is needed.
// M410 skips the queue (it still waits for the running command: no EMERGENCY_PARSER), the
// rest follows through the normal queue.
esp_err_t printer_state_soft_stop(printer_source_t source)
{
    xSemaphoreTake(s_mux, portMAX_DELAY);
    s.stopped = true;
    s.home_abort = true;
    unhome();  // M410 can lose steps, and M84 drops the position anyway
    s.relative = false;
    xSemaphoreGive(s_mux);
    esp_event_post(PRINTER_STATE_EVENT, PRINTER_STATE_EVENT_CHANGED, NULL, 0, 0);

    esp_err_t err = printer_emergency("M410", source);
    // G90 / M121: a home or jog stopped halfway may have left relative mode and endstops on.
    static const char *const after[] = {"M104 S0", "M140 S0", "M107", "M84", "G90", "M121"};
    for (size_t i = 0; i < sizeof(after) / sizeof(after[0]) && err == ESP_OK; i++) {
        err = printer_send(after[i], source, pdMS_TO_TICKS(200));
    }
    return err;
}

void printer_state_restart(void)
{
    xSemaphoreTake(s_mux, portMAX_DELAY);
    bool changed = s.stopped;
    s.stopped = false;
    xSemaphoreGive(s_mux);
    if (changed) {
        esp_event_post(PRINTER_STATE_EVENT, PRINTER_STATE_EVENT_CHANGED, NULL, 0, 0);
    }
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
