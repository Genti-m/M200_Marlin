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
// "M104S200"), so the number may be followed directly by the next word. "G28.1" -> -1.
int gcode_command_code(const char *line, char letter, const char **rest);

// Value of one word in what follows the command code (" S200", 'S' -> 200). A word starts
// after anything that isn't a letter, so "X10Y20" is two words like Marlin reads it.
// Stops at a ';' comment or '*' checksum.
bool gcode_num_param(const char *rest, char letter, float *out);

// Target of a heater command: S, or R for M109/M190 ("wait, also when cooling").
bool gcode_heater_target(const char *rest, float *out);

// Machine position (0 = switch) of logical position l on axis a.
float gcode_machine(const gcode_axis_t *a, float l);

// Forget every home (M84, STOP, reset, kill, dropped line).
void gcode_unhome(gcode_motion_t *m);

// Why the laser build must not send this line, or NULL:
//  - a heater target above 0 (M104/M109/M140/M190 S/R), M303: no heaters, MINTEMP kill
//  - G28, G29, G30, M48: they preheat for the Z probe
//  - G0-G3 taking a homed axis outside 0..max_mm[axis] of its switch (arcs: end point only;
//    NaN fails too)
// The limit message is written to buf; the others are string constants.
const char *gcode_refusal(const gcode_motion_t *m, const float max_mm[3], const char *line,
                          char *buf, size_t buf_len);

// Follow a line on its way to the printer: G90/G91, G92 and G0-G3 update the homed axes.
// A G92 naming an axis in home_mask marks it homed with dir +1 (X, Y) / -z_dir (Z).
void gcode_track(gcode_motion_t *m, const char *line, float z_dir);

#ifdef __cplusplus
}
#endif
