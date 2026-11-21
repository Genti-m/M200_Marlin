// gcode_guard: the G-code rules of the laser build (road_Change.md ch. 2, 3, 5) and the axis
// tracking behind the 0..max limits. Pure C, no ESP-IDF and no locking: printer_state holds its
// mutex around these calls. Builds on the PC for the host tests (see test/).
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// One axis as the bridge tracks it from the lines it lets through (Marlin is never homed:
// no G28). l is Marlin's logical position, zero the logical position of the switch found by
// the bridge's home (G92 shifts both), dir +1 when Marlin's numbers grow away from the switch
// (-1 for Z with z_dir +1). The machine position, 0..max, is dir * (l - zero).
typedef struct {
    bool homed;
    float l, zero, dir;
} gcode_axis_t;

typedef struct {
    gcode_axis_t ax[3];  // X Y Z
    bool relative;       // G91
    uint8_t home_mask;   // axes the next G92 marks homed (end of a bridge home task)
} gcode_motion_t;

// Code of a G/M word at the start of the line ("N12 G28 X" -> 28 for 'G'), else -1.
// *rest (if not NULL) points behind the number. Marlin needs no spaces ("N12G28X",
// "M104S200"), so the number may be followed directly by the next word, and like Marlin it
// skips spaces between letter and number ("M 104" -> 104). "G28.1" -> -1.
int gcode_command_code(const char *line, char letter, const char **rest);

// Value of one word in what follows the command code (" S200", 'S' -> 200). A word starts
// after anything that isn't a letter, so "X10Y20" is two words like Marlin reads it.
// Stops at a ';' comment or '*' checksum.
bool gcode_num_param(const char *rest, char letter, float *out);

// Target of a heater command: S, or R for M109/M190 ("wait, also when cooling").
bool gcode_heater_target(const char *rest, float *out);

// 112 / 108 / 410 when the line is M112 / M108 / M410 as Marlin reads it ("N5 M 410" too),
// else 0. These skip the bridge queue (printer_emergency()).
int gcode_emergency_code(const char *line);

// Machine position (0 = switch) of logical position l on axis a.
float gcode_machine(const gcode_axis_t *a, float l);

// Forget every home (M84, STOP, reset, kill, dropped line).
void gcode_unhome(gcode_motion_t *m);

// Why the laser build must not send this line, or NULL:
//  - a heater target above 0 (M104/M109/M140/M190 S/R), any preset target (I), M303: no
//    heaters, MINTEMP kill
//  - G28, G29, G30, M48: they preheat for the Z probe
//  - G0-G3 taking a homed axis outside 0..max_mm[axis] of its switch (arcs: end point only;
//    NaN fails too)
// The limit message is written to buf; the others are string constants.
const char *gcode_refusal(const gcode_motion_t *m, const float max_mm[3], const char *line,
                          char *buf, size_t buf_len);

// Follow a line on its way to the printer: G90/G91, G92 and G0-G3 update the homed axes.
// A G92 naming an axis in home_mask marks it homed with dir +1 (X, Y) / -z_dir (Z).
void gcode_track(gcode_motion_t *m, const char *line, float z_dir);

// ---------------------------------------------------------------- laser (road_Change.md ch. 8)
// Stock Marlin has no laser feature, so the laser hangs on a fan output and the bridge turns
// GRBL-style laser G-code (what LaserGRBL / LightBurn write) into M106 on that fan, like GRBL's
// laser mode: M3/M4 S<power> only sets the power, the laser fires during G1/G2/G3 moves and is
// off during G0 travel and G4 dwells. M106 sits between the moves in Marlin's queue: Marlin
// stores the fan speed in the planner blocks after it, but writes it to the output in a 10 Hz
// poll (check_axes_activity), so a change lands up to ~100 ms after its block starts.

typedef struct {
    int fan;      // Marlin fan index the laser is on (M106 P<fan>)
    float s_max;  // the S that means full power in M3/M4 and on moves (1000 like GRBL, or 255)
    float cap;    // 0..1: the highest share of full PWM ever sent
} gcode_laser_cfg_t;

typedef struct {
    bool on;      // M3/M4 given, until M5 / M107 / disarm
    float s;      // last S (modal, as in GRBL)
    int pwm;      // 0..255: what the bridge last queued for the laser fan
} gcode_laser_t;

typedef struct {
    int pre_pwm;         // -1: nothing, else queue "M106 P<fan> S<pre_pwm>" before the line
    bool send;           // the line itself goes to Marlin (M3/M4/M5 and a lone "S500" don't)
    gcode_laser_t next;  // the laser after this line, once both are queued
} gcode_laser_step_t;

// PWM (0..255) for power s, capped.
int gcode_laser_pwm(const gcode_laser_cfg_t *c, float s);

// What a line does to the laser, or why it is refused (a string constant):
//  - M3/M4 [S]: power on for the next moves. Refused unless armed when the power is above 0.
//    M4 is the same as M3 (stock Marlin can't scale the power with the speed).
//  - M5: off. Always allowed.
//  - M106 on the laser's fan with S above 0 (or no S: full speed in Marlin): refused, use M3.
//    M106 S0 / M107 on it: off. Other fans pass untouched.
//  - G1/G2/G3 [S]: the laser at the power (0 when not on or not armed). G0 [S] and G4: off.
//  - a lone "S500": new power, not sent. A negative or NaN S is refused.
const char *gcode_laser_plan(const gcode_laser_t *l, const gcode_laser_cfg_t *c, bool armed,
                             const char *line, gcode_laser_step_t *out);

// Most lines one line can take in the printer queue (2 when a laser line may go before it), for
// a batch that must go in whole.
int gcode_laser_cost(const char *line);

#ifdef __cplusplus
}
#endif
