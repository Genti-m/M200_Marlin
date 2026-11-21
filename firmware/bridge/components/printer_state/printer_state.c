#include "printer_state.h"

#include <ctype.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "driver/gpio.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "gcode_guard.h"
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
    // Laser: the module isn't chosen yet (road_Change.md ch. 8), so the cap starts at 100 %
    // and the hardware decides; the focus pulse starts low.
    [PS_LASER_FAN]     = {"laser_fan", 0, 0, 7, 1},
    [PS_LASER_S_MAX]   = {"laser_s_max", 1000, 1, 10000, 1},
    [PS_LASER_CAP]     = {"laser_cap", 100, 1, 100, 1},
    [PS_LASER_TEST]    = {"laser_test", 2, 0.5f, 20, 0.5f},
    [PS_LASER_TEST_MS] = {"laser_test_ms", 300, 50, 2000, 50},
    [PS_LASER_LEASE]   = {"laser_lease", 3, 2, 30, 1},  // the page says "alive" every 0.7 s
};

// ---------------------------------------------------------------- laser hardware
// The optional enable pin (Kconfig LASER_ENABLE_GPIO): off at boot, on only while armed.
static bool s_pin_ok;   // configured and usable
static bool s_pin_bad;  // configured but not usable: arming refused

#if CONFIG_LASER_ENABLE_GPIO >= 0
#define LASER_PIN CONFIG_LASER_ENABLE_GPIO
#ifdef CONFIG_LASER_ENABLE_ACTIVE_LOW
#define LASER_PIN_ON 0
#else
#define LASER_PIN_ON 1
#endif

static void laser_pin(bool on)
{
    if (s_pin_ok) {
        gpio_set_level(LASER_PIN, on ? LASER_PIN_ON : !LASER_PIN_ON);
    }
}

// ESP32-S3 pins that must not carry the laser enable: the printer UART, USB D-/D+ (the USB
// console, 19/20), SPI flash and the octal PSRAM of the S3R8 (26..37), and the strapping pins
// (0, 3, 45, 46), whose level at reset (the laser pull-down) picks the boot mode.
static bool laser_pin_usable(int pin)
{
    return GPIO_IS_VALID_OUTPUT_GPIO(pin) && pin != CONFIG_PRINTER_LINK_TX_GPIO &&
           pin != CONFIG_PRINTER_LINK_RX_GPIO && pin != 19 && pin != 20 &&
           !(pin >= 26 && pin <= 37) && pin != 0 && pin != 3 && pin != 45 && pin != 46;
}

static void laser_pin_init(void)
{
    if (!laser_pin_usable(LASER_PIN)) {
        ESP_LOGE(TAG, "laser enable GPIO %d is not a free output (UART, USB, flash/PSRAM or "
                 "strapping pin): arming refused", LASER_PIN);
        s_pin_bad = true;
        return;
    }
    // Off level first, then the output, with the internal pull toward off. Not gpio_reset_pin():
    // it turns the pull-up on, a moment toward "on" for an active-high driver.
    gpio_set_level(LASER_PIN, !LASER_PIN_ON);
    const gpio_config_t io = {
        .pin_bit_mask = 1ULL << LASER_PIN,
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = LASER_PIN_ON ? GPIO_PULLUP_DISABLE : GPIO_PULLUP_ENABLE,
        .pull_down_en = LASER_PIN_ON ? GPIO_PULLDOWN_ENABLE : GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    s_pin_ok = gpio_config(&io) == ESP_OK && gpio_set_level(LASER_PIN, !LASER_PIN_ON) == ESP_OK;
    s_pin_bad = !s_pin_ok;
}
#else  // no enable pin: the fan output alone switches the laser
static void laser_pin(bool on) { (void)on; }
static void laser_pin_init(void) {}
#endif

// Laser on but nothing new queued for this long: M400, then off (motion has stopped).
#define LASER_IDLE_OFF_US (1000 * 1000)

// What a disarm still has to do, done by the laser task (it may block on the queue).
enum {
    LASER_DROP_NONE,  // nothing to send: Marlin restarted or can't read (kill)
    LASER_DROP_OFF,   // a laser-off line
    LASER_DROP_STOP,  // soft STOP if the laser may be firing or lines are queued, else laser off
};

static struct {
    float cfg[PS_COUNT];  // settings
    marlin_temp_t nozzle, bed;
    gcode_motion_t mo;   // homed axes, G90/G91, home_mask (gcode_guard)
    bool homing;        // a home task is running: user G-code refused
    bool home_abort;     // set by STOP / kill / reset / emergency: the running home gives up.
                         // Only a new home clears it, so RESTART or a reboot can't revive one.
    bool sw[3];          // last M119: x_min, y_min, z_probe pressed
    bool pos_valid;
    float pos[3];
    int64_t last_busy_us;
    bool stopped;  // soft STOP pressed, until printer_state_restart()
    bool halted;   // Marlin called kill(): no input until the printer is reset ("start")
    gcode_laser_t laser;      // M3 on, S, the laser fan's PWM as queued (gcode_guard)
    bool armed;
    int64_t lease_until_us;   // armed until then, unless a client says "alive"
    int64_t last_submit_us;   // last accepted user line: the idle-off watch
    bool drop_pending;        // a disarm for the laser task to finish
    int drop_mode;            // LASER_DROP_*: the strongest since the last one was finished
    const char *drop_why;     // its note (a constant), or NULL
} s;

static SemaphoreHandle_t s_sw_sem;  // given on every M119 "z_probe:" line (its last one)
// Held from the guard to the queue by every user line and laser line: a laser line and the
// move it belongs to go in one after the other, no other client's line between them.
static SemaphoreHandle_t s_submit;
static TaskHandle_t s_laser_task;

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

// Disarm: the enable pin goes off here and now, the rest (note, laser-off line or STOP) is
// left to the laser task. why: a note (a constant, no "kill" / "halted"), or NULL.
static void laser_drop_locked(const char *why, int mode)
{
    if (!s.armed) {
        return;
    }
    s.armed = false;
    laser_pin(false);
    s.laser.on = false;
    if (!s.drop_pending || mode > s.drop_mode) {
        s.drop_mode = mode;
    }
    if (why) {
        s.drop_why = why;
    }
    s.drop_pending = true;
    if (s_laser_task) {
        xTaskNotifyGive(s_laser_task);
    }
}

// Marlin restarted or stopped reading: its fans are off (restart), or nothing can be sent.
static void laser_reset_locked(const char *why)
{
    laser_drop_locked(why, LASER_DROP_NONE);
    s.laser.on = false;
    s.laser.pwm = 0;
}

static void unhome(void)
{
    // The laser needs the homes (its limits): losing them disarms it.
    laser_drop_locked("Error:bridge: laser disarmed: homes lost", LASER_DROP_STOP);
    gcode_unhome(&s.mo);
    s.pos_valid = false;
}

static bool any_homed(void)
{
    return s.mo.ax[0].homed || s.mo.ax[1].homed || s.mo.ax[2].homed;
}

static bool unplugged(const marlin_temp_t *t)
{
    return t->valid && t->actual < UNPLUGGED_BELOW_C;
}

// kill() turns every heater off; Marlin can't report that any more, so show it ourselves.
static void halt(void)
{
    // Marlin reads nothing more, and its kill() / minkill() don't zero the fans (bugfix-2.1.x
    // source); minkill() also turns interrupts off, so a software-PWM fan output freezes as it
    // is. Only the enable pin (off now) or the hardware E-stop cut the laser.
    laser_drop_locked("Error:bridge: laser disarmed: Marlin stopped, its fan output stays as it "
                      "was: cut the laser power", LASER_DROP_NONE);
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
    int g = gcode_command_code(ev->text, 'G', &rest);
    int m = gcode_command_code(ev->text, 'M', g < 0 ? &rest : NULL);
    float target;
    if ((m == 104 || m == 109 || m == 140 || m == 190) && gcode_heater_target(rest, &target)) {
        // Show a new target at once (Off -> 0), the next temp report confirms it.
        marlin_temp_t *t = (m == 140 || m == 190) ? &s.bed : &s.nozzle;
        t->target = target;
        return t->valid;
    }
    if (ev->emergency) {
        s.home_abort = true;  // its queued lines are dropped
        // M410 / M108 cleared the queue: only the laser-off line is left to send.
        laser_drop_locked("Error:bridge: laser disarmed: emergency command", LASER_DROP_OFF);
    }
    if (m == 84 || m == 18 || m == 112 || ev->emergency) {
        bool changed = s.pos_valid || any_homed();
        unhome();
        return changed;
    }
    return false;
}

// Follow a line on its way to the printer (accepted user line or a home task line). s_mux held.
static void track(const char *line)
{
    gcode_track(&s.mo, line, s.cfg[PS_Z_DIR]);
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
        laser_reset_locked("Error:bridge: laser disarmed: printer restarted");
        unhome();
        s.home_abort = true;  // a home's moves must not go on into a fresh Marlin
        s.mo.relative = false;  // Marlin boots in G90
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
        if (*(const printer_link_state_t *)data != PRINTER_LINK_UP) {
            laser_drop_locked("Error:bridge: laser disarmed: the printer stopped answering",
                              LASER_DROP_STOP);
        }
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

// True when these values can be used together, else false and why not in why.
static bool settings_check(const float *v, char why[PRINTER_STATE_WHY_MAX])
{
    const char *msg = NULL;
    for (int i = 0; i < PS_COUNT; i++) {
        const printer_setting_desc_t *d = &SETTINGS[i];
        if (!(v[i] >= d->min && v[i] <= d->max)) {
            snprintf(why, PRINTER_STATE_WHY_MAX, "Error:bridge: setting %s must be %g..%g",
                     d->key, (double)d->min, (double)d->max);
            return false;
        }
    }
    if (v[PS_Z_DIR] != -1 && v[PS_Z_DIR] != 1) {
        msg = "Error:bridge: setting z_dir must be -1 or 1";
    }
    for (int i = 0; i < 3 && !msg; i++) {
        if (v[PS_PARK] >= v[PS_MAX_X + i]) {
            msg = "Error:bridge: setting park must be below every axis max";
        }
    }
    if (!msg && v[PS_BACKOFF] >= v[PS_PARK]) {
        msg = "Error:bridge: setting backoff must be below park";
    }
    if (!msg && (v[PS_XY_SLOW_F] > v[PS_XY_FAST_F] || v[PS_Z_SLOW_F] > v[PS_Z_FAST_F] ||
                 v[PS_Z_SLOW_STEP] > v[PS_Z_FAST_STEP])) {
        msg = "Error:bridge: the slow pass can't be faster or coarser than the seek";
    }
    if (!msg && v[PS_LASER_FAN] != floorf(v[PS_LASER_FAN])) {
        msg = "Error:bridge: setting laser_fan must be a whole number";
    }
    if (!msg && v[PS_LASER_TEST] > v[PS_LASER_CAP]) {
        msg = "Error:bridge: setting laser_test can't be above laser_cap";
    }
    if (msg) {
        strlcpy(why, msg, PRINTER_STATE_WHY_MAX);
    }
    return msg == NULL;
}

void printer_state_get_settings(float out[PS_COUNT])
{
    xSemaphoreTake(s_mux, portMAX_DELAY);
    memcpy(out, s.cfg, sizeof(s.cfg));
    xSemaphoreGive(s_mux);
}

static void post_note(const char *text);

bool printer_state_set_settings(const float in[PS_COUNT], char why[PRINTER_STATE_WHY_MAX])
{
    bool ok = settings_check(in, why);
    xSemaphoreTake(s_mux, portMAX_DELAY);
    if (ok && s.homing) {
        strlcpy(why, "Error:bridge: settings can't change while homing", PRINTER_STATE_WHY_MAX);
        ok = false;
    }
    if (ok && s.armed) {
        strlcpy(why, "Error:bridge: settings can't change while the laser is armed",
                PRINTER_STATE_WHY_MAX);
        ok = false;
    }
    bool same = ok && memcmp(in, s.cfg, sizeof(s.cfg)) == 0;
    if (ok && !same) {
        memcpy(s.cfg, in, sizeof(s.cfg));
    }
    xSemaphoreGive(s_mux);
    if (!ok) {
        post_note(why);
        return false;
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
    return true;
}

// ---------------------------------------------------------------- API

static void laser_task(void *arg);
static void laser_off_line(char *buf, size_t len);

esp_err_t printer_state_start(void)
{
    if (s_mux) {
        return ESP_ERR_INVALID_STATE;
    }
    laser_pin_init();  // first: the enable pin is off from here on
    s_mux = xSemaphoreCreateMutex();
    s_sw_sem = xSemaphoreCreateBinary();
    s_submit = xSemaphoreCreateMutex();
    if (!s_mux || !s_sw_sem || !s_submit) {
        return ESP_ERR_NO_MEM;
    }
    settings_load(s.cfg);
    char why[PRINTER_STATE_WHY_MAX];
    if (!settings_check(s.cfg, why)) {  // each one in range, but not together: back to defaults
        ESP_LOGW(TAG, "saved settings rejected (%s): defaults", why);
        for (int i = 0; i < PS_COUNT; i++) {
            s.cfg[i] = SETTINGS[i].def;
        }
    }
    esp_err_t err = esp_event_handler_register(PRINTER_EVENT, ESP_EVENT_ANY_ID, on_printer_event, NULL);
    if (err != ESP_OK) {
        return err;
    }
    if (xTaskCreate(laser_task, "laser", 3072, NULL, 5, &s_laser_task) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }
    // The printer may already be running (bridge reset): its "start" won't come again, and
    // the laser fan may still be on from before the reset.
    char off[24];
    laser_off_line(off, sizeof(off));
    printer_send(off, PRINTER_SRC_INTERNAL, pdMS_TO_TICKS(100));
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
        const gcode_axis_t *a = &s.mo.ax[i];
        out->homed[i] = a->homed;
        out->mpos[i] = gcode_machine(a, a->l);
        out->zero[i] = a->zero;
        out->dir[i] = a->homed ? a->dir : 1;
        out->max_mm[i] = s.cfg[PS_MAX_X + i];
    }
    out->homing = s.homing;
    out->stopped = s.stopped;
    out->halted = s.halted;
    out->nozzle_unplugged = unplugged(&s.nozzle);
    out->bed_unplugged = unplugged(&s.bed);
    out->pos_valid = s.pos_valid;
    memcpy(out->pos, s.pos, sizeof(out->pos));
    out->laser_armed = s.armed;
    out->laser_on = s.laser.on;
    out->laser_pwm = s.laser.pwm;
    out->laser_pin = s_pin_ok;
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

// Why a line must not go to the printer now (a constant, or written to buf), or NULL.
// s_mux held.
static const char *refusal(const char *line, printer_source_t source, char *buf, size_t len)
{
    if (s.stopped && source != PRINTER_SRC_CONSOLE) {
        return "Error:bridge: stopped, press RESTART first";
    }
    if (s.homing) {
        return "Error:bridge: homing running, wait for it (STOP aborts it)";
    }
    // Heat, G28 / probing, and the 0..max limits (settings): gcode_guard, host-tested.
    const float max_mm[3] = {s.cfg[PS_MAX_X], s.cfg[PS_MAX_Y], s.cfg[PS_MAX_Z]};
    return gcode_refusal(&s.mo, max_mm, line, buf, len);
}

// s_mux held.
static gcode_laser_cfg_t laser_cfg(void)
{
    return (gcode_laser_cfg_t){.fan = (int)s.cfg[PS_LASER_FAN], .s_max = s.cfg[PS_LASER_S_MAX],
                               .cap = s.cfg[PS_LASER_CAP] / 100.0f};
}

static void laser_line_locked(char *buf, size_t len, int pwm)
{
    snprintf(buf, len, "M106 P%d S%d", (int)s.cfg[PS_LASER_FAN], pwm);
}

static void laser_off_line(char *buf, size_t len)
{
    xSemaphoreTake(s_mux, portMAX_DELAY);
    laser_line_locked(buf, len, 0);
    xSemaphoreGive(s_mux);
}

// The guard: false (and an "Error:bridge:" line to all clients) when the line must not go
// out now. An allowed line is tracked (G90/G91, G92, moves) and planned for the laser: *pre
// gets a laser line to queue first ("" for none), *send false when the line itself isn't sent.
static bool allow(const char *line, printer_source_t source, char *pre, size_t pre_len, bool *send)
{
    char buf[PRINTER_STATE_WHY_MAX];
    const char *why_note = NULL;
    gcode_laser_step_t st;
    pre[0] = '\0';
    xSemaphoreTake(s_mux, portMAX_DELAY);
    const char *why = refusal(line, source, buf, sizeof(buf));
    if (!why) {
        gcode_laser_cfg_t lc = laser_cfg();
        why = gcode_laser_plan(&s.laser, &lc, s.armed, line, &st);
    }
    if (!why) {
        track(line);
        s.laser = st.next;
        s.last_submit_us = esp_timer_get_time();
        if (st.pre_pwm >= 0) {
            laser_line_locked(pre, pre_len, st.pre_pwm);
        }
        *send = st.send;
        if (!st.send && st.next.on) {  // M3/M4 or "S..": nothing goes out, so say what it did
            gcode_laser_cfg_t lc = laser_cfg();
            snprintf(buf, sizeof(buf), "echo:bridge: laser at S%g = %.0f %%: G1/G2/G3 moves fire it",
                     (double)st.next.s, gcode_laser_pwm(&lc, st.next.s) * 100.0 / 255.0);
            why_note = buf;
        }
    }
    xSemaphoreGive(s_mux);
    if (why_note) {
        post_note(why_note);
    }
    if (why) {
        ESP_LOGW(TAG, "refused \"%s\": %s", line, why);
        post_note(why);
    }
    return why == NULL;
}

void printer_state_note(const char *text)
{
    post_note(text);
}

// An allowed line didn't make it into the queue (full). The bridge already counted it, so the
// homes are dropped (positions and G90/G91 no longer known) and an "Error:bridge:" line says so.
static void dropped(const char *line)
{
    ESP_LOGW(TAG, "printer queue full, dropped: %s", line);
    xSemaphoreTake(s_mux, portMAX_DELAY);
    bool had = any_homed();
    unhome();
    xSemaphoreGive(s_mux);
    char msg[96];
    snprintf(msg, sizeof(msg), "Error:bridge: queue full, \"%.24s\" dropped%s", line,
             had ? ": home lost, home again" : "");
    post_note(msg);
    esp_event_post(PRINTER_STATE_EVENT, PRINTER_STATE_EVENT_CHANGED, NULL, 0, 0);
}

int printer_state_submit(const char *line, printer_source_t source, TickType_t wait)
{
    int ec = gcode_emergency_code(line);
    if (ec == 112 && source != PRINTER_SRC_CONSOLE) {
        return printer_state_soft_stop(source) == ESP_OK;  // same as the STOP button
    }
    if (ec) {
        return printer_emergency(line, source) == ESP_OK;
    }
    xSemaphoreTake(s_submit, portMAX_DELAY);
    // Read before the guard: a STOP after this point makes the link drop the line.
    uint32_t epoch = printer_link_epoch();
    char pre[24];
    bool send = true;
    int r = 1;
    if (!allow(line, source, pre, sizeof(pre), &send)) {
        r = 0;
    } else if (pre[0] && printer_send_at(pre, source, wait, epoch) != ESP_OK) {
        dropped(pre);  // also disarms: the laser may be in a state the bridge doesn't know
        r = -1;
    } else if (send && printer_send_at(line, source, wait, epoch) != ESP_OK) {
        dropped(line);
        r = -1;
    }
    xSemaphoreGive(s_submit);
    return r;
}

int printer_state_line_cost(const char *line)
{
    return gcode_laser_cost(line);
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
// printer_link epoch when the home started: any emergency command (STOP, a typed M410 / M108)
// changes it, which aborts the home, and the link drops home lines queued with the old one.
static uint32_t s_home_epoch;

// Seek a bit more than the whole travel, so "not reached" means the switch is missing.
static float home_seek_mm(int axis)
{
    return hc[PS_MAX_X + axis] + 30;
}

static const char *const HOME_FAILED = "Error:bridge: homing failed (no reply or STOP)";

// s_mux held.
static bool home_aborted_locked(void)
{
    return s.home_abort || s.stopped || s.halted || printer_link_epoch() != s_home_epoch;
}

static bool home_aborted(void)
{
    xSemaphoreTake(s_mux, portMAX_DELAY);
    bool a = home_aborted_locked();
    xSemaphoreGive(s_mux);
    return a;
}

// A STOP between the check and the queue can't let the line through: it is queued with the
// home's epoch, which the STOP has already ended, so the link drops it. And it is tracked only
// if the home still runs, so a dropped "G92" can't mark an axis homed after the STOP.
static bool home_send(const char *line)
{
    if (home_aborted() ||
        printer_send_at(line, PRINTER_SRC_INTERNAL, pdMS_TO_TICKS(1000), s_home_epoch) != ESP_OK) {
        return false;
    }
    xSemaphoreTake(s_mux, portMAX_DELAY);
    bool aborted = home_aborted_locked();
    if (!aborted) {
        track(line);
    }
    xSemaphoreGive(s_mux);
    return !aborted;
}

// The X/Y home turns endstops on and goes relative. After a failure, or an abort that wasn't a
// soft STOP (which sends G90 / M121 itself) or a kill, put Marlin back: G90, M121. Both are
// harmless if they land twice.
static void home_restore_modes(void)
{
    xSemaphoreTake(s_mux, portMAX_DELAY);
    bool skip = s.stopped || s.halted;
    xSemaphoreGive(s_mux);
    if (skip) {
        return;
    }
    if (printer_send("G90", PRINTER_SRC_INTERNAL, pdMS_TO_TICKS(1000)) == ESP_OK) {
        xSemaphoreTake(s_mux, portMAX_DELAY);
        track("G90");
        xSemaphoreGive(s_mux);
    }
    printer_send("M121", PRINTER_SRC_INTERNAL, pdMS_TO_TICKS(1000));
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
    const char *why = home_send("M120") && home_send("G91") ? NULL : HOME_FAILED;
    if (!why) {
        why = home_xy_axis(0);
    }
    if (!why) {
        why = home_xy_axis(1);
    }
    if (!why && (!home_send("G90") || !home_send("M121"))) {
        why = HOME_FAILED;
    }
    if (why) {
        home_restore_modes();  // also after an abort: home_send() sends nothing then
        return why;
    }
    xSemaphoreTake(s_mux, portMAX_DELAY);
    s.mo.home_mask |= 1u << 0 | 1u << 1;
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
    s.mo.home_mask |= 1u << 2;
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
    s.mo.home_mask = 0;
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
    bool armed = s.armed;  // a home's moves bypass the laser planning
    bool busy = armed || s.homing || s.stopped || s.halted ||
                printer_link_get_state() != PRINTER_LINK_UP;
    if (!busy) {
        s.homing = true;
        s.home_abort = false;
        s_home_epoch = printer_link_epoch();
        memcpy(hc, s.cfg, sizeof(hc));
        for (int i = 0; i < 3; i++) {
            if (axes & (1u << i)) {
                s.mo.ax[i].homed = false;
            }
        }
    }
    xSemaphoreGive(s_mux);
    if (busy) {
        post_note(armed ? "Error:bridge: disarm the laser before homing"
                        : "Error:bridge: homing not possible now (running, stopped, halted or no printer)");
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
    char laser_off[24];
    xSemaphoreTake(s_mux, portMAX_DELAY);
    s.stopped = true;
    s.home_abort = true;
    // Enable pin off now; the laser-off line follows right after M410, below.
    laser_drop_locked("echo:bridge: laser disarmed (STOP)", LASER_DROP_NONE);
    s.laser.on = false;
    s.laser.pwm = 0;
    laser_line_locked(laser_off, sizeof(laser_off), 0);
    unhome();  // M410 can lose steps, and M84 drops the position anyway
    s.mo.relative = false;
    xSemaphoreGive(s_mux);
    esp_event_post(PRINTER_STATE_EVENT, PRINTER_STATE_EVENT_CHANGED, NULL, 0, 0);

    esp_err_t err = printer_emergency("M410", source);
    // G90 / M121: a home or jog stopped halfway may have left relative mode and endstops on.
    const char *const after[] = {laser_off, "M104 S0", "M140 S0", "M107", "M84", "G90", "M121"};
    for (size_t i = 0; i < sizeof(after) / sizeof(after[0]) && err == ESP_OK; i++) {
        err = printer_send(after[i], source, pdMS_TO_TICKS(200));
    }
    return err;
}

// ---------------------------------------------------------------- laser

const char *printer_state_laser_arm(void)
{
    const char *why = NULL;
    xSemaphoreTake(s_mux, portMAX_DELAY);
    if (s.armed) {
        s.lease_until_us = esp_timer_get_time() + (int64_t)(s.cfg[PS_LASER_LEASE] * 1e6f);
        xSemaphoreGive(s_mux);
        return NULL;
    }
    if (s_pin_bad) {
        why = "Error:bridge: laser not armed: its enable GPIO is not usable (see the log)";
    } else if (s.drop_pending) {  // the last disarm's off line / STOP isn't out yet
        why = "Error:bridge: laser not armed: still turning off, try again";
    } else if (printer_link_get_state() != PRINTER_LINK_UP || s.halted) {
        why = "Error:bridge: laser not armed: the printer isn't up";
    } else if (s.stopped) {
        why = "Error:bridge: laser not armed: stopped, press RESTART first";
    } else if (s.homing) {
        why = "Error:bridge: laser not armed: homing running";
    } else if (!s.mo.ax[0].homed || !s.mo.ax[1].homed) {
        why = "Error:bridge: laser not armed: home X Y first";
    } else {
        s.armed = true;
        s.laser.on = false;  // M3 has to come after the arm
        s.lease_until_us = esp_timer_get_time() + (int64_t)(s.cfg[PS_LASER_LEASE] * 1e6f);
        s.last_submit_us = esp_timer_get_time();
        laser_pin(true);
    }
    bool pin = s_pin_ok;
    xSemaphoreGive(s_mux);
    post_note(why ? why
              : pin ? "echo:bridge: laser ARMED: M3 S<power> then G1 moves fire it"
                    : "echo:bridge: laser ARMED (no enable pin: off waits for Marlin's queue)");
    esp_event_post(PRINTER_STATE_EVENT, PRINTER_STATE_EVENT_CHANGED, NULL, 0, 0);
    return why;
}

void printer_state_laser_alive(void)
{
    xSemaphoreTake(s_mux, portMAX_DELAY);
    if (s.armed) {
        s.lease_until_us = esp_timer_get_time() + (int64_t)(s.cfg[PS_LASER_LEASE] * 1e6f);
    }
    xSemaphoreGive(s_mux);
}

void printer_state_laser_disarm(void)
{
    xSemaphoreTake(s_mux, portMAX_DELAY);
    laser_drop_locked("echo:bridge: laser disarmed", LASER_DROP_STOP);
    xSemaphoreGive(s_mux);
}

// Queue a laser-off line (after M400 when wait_moves: off once the moves are done). A queue
// that stays full means the laser state is unknown: soft STOP. s_submit held by the caller.
static void laser_send_off(bool wait_moves)
{
    char off[24];
    xSemaphoreTake(s_mux, portMAX_DELAY);
    laser_line_locked(off, sizeof(off), 0);
    s.laser.pwm = 0;
    xSemaphoreGive(s_mux);
    if ((wait_moves && printer_send("M400", PRINTER_SRC_INTERNAL, pdMS_TO_TICKS(500)) != ESP_OK) ||
        printer_send(off, PRINTER_SRC_INTERNAL, pdMS_TO_TICKS(500)) != ESP_OK) {
        printer_state_note("Error:bridge: laser-off line didn't fit in the queue: STOP");
        printer_state_soft_stop(PRINTER_SRC_INTERNAL);
    }
}

const char *printer_state_laser_test(void)
{
    const char *why = NULL;
    char on[24], dwell[24], off[24];
    float pct = 0;
    int ms = 0;
    xSemaphoreTake(s_submit, portMAX_DELAY);
    uint32_t epoch = printer_link_epoch();
    int64_t now = esp_timer_get_time();
    xSemaphoreTake(s_mux, portMAX_DELAY);
    bool busy = s.last_busy_us > 0 && now - s.last_busy_us <= BUSY_HOLD_US;
    if (!s.armed) {
        why = "Error:bridge: focus pulse: arm the laser first";
    } else if (s.stopped || s.homing || s.halted) {
        why = "Error:bridge: focus pulse not possible now";
    } else if (printer_link_queue_len() > 0 || busy || s.laser.pwm > 0 || s.laser.on) {
        why = "Error:bridge: focus pulse: wait until the head stands still, laser off (M5)";
    } else if (printer_link_queue_free() < 4) {
        why = "Error:bridge: focus pulse: queue busy, try again";
    } else {
        pct = fminf(s.cfg[PS_LASER_TEST], s.cfg[PS_LASER_CAP]);
        ms = (int)s.cfg[PS_LASER_TEST_MS];
        int pwm = (int)lroundf(pct / 100.0f * 255.0f);
        laser_line_locked(on, sizeof(on), pwm < 1 ? 1 : pwm);
        laser_line_locked(off, sizeof(off), 0);
        snprintf(dwell, sizeof(dwell), "G4 P%d", ms);
        s.laser.pwm = 0;  // where the pulse ends
        s.last_submit_us = now;
    }
    xSemaphoreGive(s_mux);
    if (!why) {
        // M400: the moves are done before it fires. If the pulse can't be queued whole, STOP:
        // its "on" may be in and its "off" not.
        const char *lines[] = {"M400", on, dwell, off};
        for (size_t i = 0; i < 4; i++) {
            if (printer_send_at(lines[i], PRINTER_SRC_INTERNAL, pdMS_TO_TICKS(500), epoch) != ESP_OK) {
                why = "Error:bridge: focus pulse didn't fit in the queue: STOP";
                break;
            }
        }
    }
    xSemaphoreGive(s_submit);
    if (why) {
        post_note(why);
        if (strstr(why, "STOP")) {
            printer_state_soft_stop(PRINTER_SRC_INTERNAL);
        }
        return why;
    }
    char note[64];
    snprintf(note, sizeof(note), "echo:bridge: laser focus pulse: %.1f %% for %d ms", (double)pct, ms);
    post_note(note);
    return NULL;
}

// Finishes disarms (note, then a STOP or a laser-off line), runs out the lease, and turns a
// laser that is on but gets nothing new off once the moves are done.
static void laser_task(void *arg)
{
    for (;;) {
        ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(200));
        int64_t now = esp_timer_get_time();
        xSemaphoreTake(s_mux, portMAX_DELAY);
        if (s.armed && now > s.lease_until_us) {
            laser_drop_locked("Error:bridge: laser disarmed: no keep-alive (page closed, hidden or "
                              "offline)", LASER_DROP_STOP);
        }
        bool pending = s.drop_pending;
        int mode = s.drop_mode;
        const char *why = s.drop_why;
        s.drop_pending = false;
        s.drop_mode = LASER_DROP_NONE;
        s.drop_why = NULL;
        bool firing = s.laser.pwm > 0;
        bool can_send = !s.halted && !s.stopped;  // a STOP sent the laser-off line itself
        bool idle = !pending && s.armed && firing && now - s.last_submit_us > LASER_IDLE_OFF_US;
        xSemaphoreGive(s_mux);

        if (pending) {
            if (why) {
                post_note(why);
            }
            if (mode == LASER_DROP_STOP && can_send && (firing || printer_link_queue_len() > 0)) {
                printer_state_soft_stop(PRINTER_SRC_INTERNAL);
            } else if (mode != LASER_DROP_NONE && can_send) {
                xSemaphoreTake(s_submit, portMAX_DELAY);
                laser_send_off(false);
                xSemaphoreGive(s_submit);
            }
            esp_event_post(PRINTER_STATE_EVENT, PRINTER_STATE_EVENT_CHANGED, NULL, 0, 0);
        } else if (idle && printer_link_queue_len() == 0) {
            xSemaphoreTake(s_submit, portMAX_DELAY);
            // Again under s_submit: a line may have come in meanwhile.
            xSemaphoreTake(s_mux, portMAX_DELAY);
            idle = s.armed && s.laser.pwm > 0 &&
                   esp_timer_get_time() - s.last_submit_us > LASER_IDLE_OFF_US;
            xSemaphoreGive(s_mux);
            if (idle && printer_link_queue_len() == 0) {
                ESP_LOGI(TAG, "laser on with nothing queued: off after the moves");
                laser_send_off(true);
                esp_event_post(PRINTER_STATE_EVENT, PRINTER_STATE_EVENT_CHANGED, NULL, 0, 0);
            }
            xSemaphoreGive(s_submit);
        }
    }
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
