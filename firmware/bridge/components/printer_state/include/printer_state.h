// printer_state: what the bridge knows about the printer, built from the printer_link events.
//
// Tracks temperatures, busy, stopped/halted, heater targets (updated as soon as an M104/M140
// is sent), the last M114 position, plus a history of console lines and of temperatures.
// Homing is the bridge's own (no G28, see printer_state_home_xy/z): per axis it tracks homed
// and the machine position (0 = the switch) from the lines it lets through, cleared by
// M84/M18/M112/STOP/"start"/kill.
// Also sends "M155 S2" at start and after every printer reset, so Marlin reports temps by itself.
// Posts PRINTER_STATE_EVENT_CHANGED on the default loop after every change or temp report.
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"
#include "esp_event.h"
#include "printer_link.h"

#ifdef __cplusplus
extern "C" {
#endif

#define PRINTER_STATE_LINES 100   // console history
#define PRINTER_STATE_TEMPS 300   // temp history, one sample per >= 2 s report: 10 min

ESP_EVENT_DECLARE_BASE(PRINTER_STATE_EVENT);
enum {
    PRINTER_STATE_EVENT_CHANGED,   // no data
    PRINTER_STATE_EVENT_SETTINGS,  // the settings changed, no data
};

// Settings ("Setting tweaking" in the web page, road_Change.md ch. 6), kept in NVS. One float
// each; printer_setting_desc() gives the JSON / NVS key, default and allowed range.
typedef enum {
    PS_MAX_X, PS_MAX_Y, PS_MAX_Z,  // mm: a homed axis stays within 0..max of its switch
    PS_PARK,                       // mm: after a home the axis parks here, off the switch
    PS_BACKOFF,                    // mm: back off the switch before the slow pass
    PS_XY_FAST_F, PS_XY_SLOW_F,    // mm/min: X Y homing, seek and slow pass
    PS_Z_DIR,                      // -1 / +1: the Z direction toward its switch
    PS_Z_FAST_STEP, PS_Z_SLOW_STEP,  // mm per M119 check: Z seek, slow pass
    PS_Z_FAST_F, PS_Z_SLOW_F,      // mm/min
    PS_JOG_F_XY, PS_JOG_F_Z,       // mm/min: web jogs
    PS_PRESET_F,                   // mm/min: web preset moves
    PS_PRESET_EDGE,                // %: preset frames at edge / 50 / 100-edge
    PS_COUNT
} printer_setting_t;

typedef struct {
    const char *key;  // also the NVS key (max 15 chars)
    float def, min, max;
    float step;       // for the web page's +/- buttons
} printer_setting_desc_t;

const printer_setting_desc_t *printer_setting_desc(printer_setting_t i);
void printer_state_get_settings(float out[PS_COUNT]);
// Check, apply and save in NVS. NULL when done, else why not (nothing changed). Refused while
// a home runs. Posts PRINTER_STATE_EVENT_SETTINGS and an "echo:bridge:" line.
const char *printer_state_set_settings(const float in[PS_COUNT]);

typedef struct {
    printer_link_state_t link;
    marlin_temp_t nozzle;
    marlin_temp_t bed;
    bool busy;       // a "busy:" line within the last 3 s
    bool homed[3];   // X Y Z homed by the bridge
    float mpos[3];   // machine position of the homed axes: 0 = switch, up to PS_MAX_X/Y/Z
    float zero[3];   // Marlin's logical position of the switch (G92 after the home shifts it)
    float dir[3];    // +1: Marlin's numbers grow away from the switch, -1 the other way.
                     // logical = zero + dir * machine
    bool homing;     // a home task is running
    bool stopped;    // soft STOP pressed: web G-code refused until printer_state_restart()
    bool halted;     // Marlin kill() (M112, MINTEMP, ...): needs a printer reset, targets shown 0
    bool nozzle_unplugged;  // reported temp below MINTEMP (5 C): sensor open, heating refused
    bool bed_unplugged;
    bool pos_valid;
    float pos[3];    // X Y Z from the last M114
    float max_mm[3]; // the limits (settings PS_MAX_X/Y/Z)
    uint32_t queue;  // lines waiting in printer_link
} printer_state_t;

typedef struct {
    bool tx;
    printer_source_t source;  // tx only
    char text[PRINTER_LINE_MAX];
} printer_hist_line_t;

typedef struct {
    int64_t time_us;          // esp_timer_get_time()
    int16_t nozzle_x10, nozzle_target_x10, bed_x10, bed_target_x10;
} printer_temp_sample_t;

esp_err_t printer_state_start(void);  // after printer_link_start()

void printer_state_get(printer_state_t *out);

// Soft STOP: M410 ahead of the queue (queue cleared), then heaters 0, fan off, motors off.
// Marlin keeps running. Sets "stopped" until printer_state_restart().
esp_err_t printer_state_soft_stop(printer_source_t source);
void printer_state_restart(void);

// Guard for user G-code, call before printer_send(). False (and an "Error:bridge: ..." line
// to all clients) when the line must not go out now:
//  - stopped (except PRINTER_SRC_CONSOLE, the debug console)
//  - a home running (printer_state_home_xy/z)
//  - laser build, no heaters: any heater target above 0 (M104/M109/M140/M190) and M303,
//    Marlin would kill() on MINTEMP / heating failed
//  - every G28, G29, G30, M48: they preheat for the Z probe, Marlin would kill() on MINTEMP
//  - G0-G3 taking a homed axis outside 0..max (settings) of its switch (arcs: end point)
// An allowed line also updates the tracked position (G90/G91, G92, moves).
bool printer_state_allow(const char *line, printer_source_t source);

// An allowed line didn't make it into the queue (full). The bridge already counted it, so the
// homes are dropped (positions and G90/G91 no longer known) and an "Error:bridge:" line says so.
void printer_state_dropped(const char *line);

// Homing without G28 (it preheats for the Z probe: MINTEMP kill), each in its own task.
// X Y: 1 mm steps toward the min endstop with M119 after each (Marlin doesn't stop a plain move
// on these switches, even with M120), back off, then 0.1 mm steps.
// Z: steps toward the switch on the probe line (0.5 mm), reading M119 after each step, 1 mm
// back, then 0.1 mm steps. Then G92 sets the switch as 0 and the axis parks at 10 mm.
// (The numbers are the default settings.)
// User G-code is refused while it runs; STOP aborts it. Progress and result arrive as
// "echo:bridge:" / "Error:bridge:" lines.
esp_err_t printer_state_home_xy(void);
esp_err_t printer_state_home_z(void);

// Copy the history, oldest first. Return the number of entries written (<= max).
size_t printer_state_copy_lines(printer_hist_line_t *out, size_t max);
size_t printer_state_copy_temps(printer_temp_sample_t *out, size_t max);

const char *printer_source_name(printer_source_t source);

#ifdef __cplusplus
}
#endif
